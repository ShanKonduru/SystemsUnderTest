// UiaReport - UI Automation readiness report, native UIA3 view.
//
// Shows what tools built on Windows' native UI Automation (COM, UIA3) can see in a desktop app: Inspect.exe,
// WinAppDriver / the Appium "Windows" driver, FlaUI. tools\uia_report_nova.ps1 is the NovaWindows view of the
// same app; tools\uia_report.bat runs both. Same parameters and grading as the PowerShell report:
//
//   good    stable AutomationId (WinForms ids that are only window handles do not count)
//   fair    no stable AutomationId, but a Name
//   poor    neither: only position-based XPath or image recognition can reach it
//   opaque  a pane with no name and no children: custom-drawn content UI Automation cannot see
//
// Usage:
//   UiaReport.exe -Exe app\bin\PolicyDesk.exe [-Arguments "..."] [-KeepOpen]
//   UiaReport.exe -ProcessName PolicyDesk | -WindowTitle "Harbor Mutual Policy Desk 3.0"
//   options: -WaitSeconds 20 -MaxDepth 25 -Csv out.csv -Json out.json -Quiet
//
// Read-only: it never clicks or types. Password field values are never read.
// Build: tools\build_tools.bat (or build_tools.sh) - C# 5, .NET Framework 4.x, interop from make_interop.ps1.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using Interop.UIAutomationClient;

namespace UiaReport
{
    class Options
    {
        public string Exe, Arguments = "", ProcessName, WindowTitle, Csv, Json;
        public int WaitSeconds = 20, MaxDepth = 25;
        public bool KeepOpen, Quiet;

        public static Options Parse(string[] args)
        {
            var o = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                string key = args[i].TrimStart('-').ToLowerInvariant();
                Func<string> next = () =>
                {
                    if (i + 1 >= args.Length) throw new ArgumentException(args[i] + " needs a value");
                    return args[++i];
                };
                switch (key)
                {
                    case "exe": o.Exe = next(); break;
                    case "arguments": o.Arguments = next(); break;
                    case "processname": o.ProcessName = next(); break;
                    case "windowtitle": o.WindowTitle = next(); break;
                    case "waitseconds": o.WaitSeconds = int.Parse(next()); break;
                    case "maxdepth": o.MaxDepth = int.Parse(next()); break;
                    case "csv": o.Csv = next(); break;
                    case "json": o.Json = next(); break;
                    case "keepopen": o.KeepOpen = true; break;
                    case "quiet": o.Quiet = true; break;
                    case "h": case "help": case "?": throw new ArgumentException("");
                    default: throw new ArgumentException("unknown option " + args[i]);
                }
            }
            if (o.Exe == null && o.ProcessName == null && o.WindowTitle == null)
                throw new ArgumentException("give -Exe, -ProcessName or -WindowTitle");
            return o;
        }
    }

    class Row
    {
        public int Depth { get; set; }
        public string ControlType { get; set; }
        public string AutomationId { get; set; }
        public string Name { get; set; }
        public string ClassName { get; set; }
        public bool Enabled { get; set; }
        public int Children { get; set; }
        public string Patterns { get; set; }
        public string Value { get; set; }
        public string Grade { get; set; }
        public string Path { get; set; }
    }

    static class Program
    {
        static readonly IUIAutomation Uia = new CUIAutomation();

        const int ProcessIdProperty = 30002, IsPasswordProperty = 30019, ValuePattern = 10002;

        static readonly Dictionary<int, string> ControlTypes = new Dictionary<int, string>
        {
            {50000, "Button"}, {50001, "Calendar"}, {50002, "CheckBox"}, {50003, "ComboBox"}, {50004, "Edit"},
            {50005, "Hyperlink"}, {50006, "Image"}, {50007, "ListItem"}, {50008, "List"}, {50009, "Menu"},
            {50010, "MenuBar"}, {50011, "MenuItem"}, {50012, "ProgressBar"}, {50013, "RadioButton"},
            {50014, "ScrollBar"}, {50015, "Slider"}, {50016, "Spinner"}, {50017, "StatusBar"}, {50018, "Tab"},
            {50019, "TabItem"}, {50020, "Text"}, {50021, "ToolBar"}, {50022, "ToolTip"}, {50023, "Tree"},
            {50024, "TreeItem"}, {50025, "Custom"}, {50026, "Group"}, {50027, "Thumb"}, {50028, "DataGrid"},
            {50029, "DataItem"}, {50030, "Document"}, {50031, "SplitButton"}, {50032, "Window"}, {50033, "Pane"},
            {50034, "Header"}, {50035, "HeaderItem"}, {50036, "Table"}, {50037, "TitleBar"}, {50038, "Separator"},
            {50039, "SemanticZoom"}, {50040, "AppBar"},
        };

        // "Is<Pattern>PatternAvailable" property ids -> pattern names a driver can use.
        static readonly KeyValuePair<int, string>[] PatternProperties =
        {
            new KeyValuePair<int, string>(30031, "Invoke"), new KeyValuePair<int, string>(30043, "Value"),
            new KeyValuePair<int, string>(30041, "Toggle"), new KeyValuePair<int, string>(30036, "SelectionItem"),
            new KeyValuePair<int, string>(30037, "Selection"), new KeyValuePair<int, string>(30028, "ExpandCollapse"),
            new KeyValuePair<int, string>(30033, "RangeValue"), new KeyValuePair<int, string>(30034, "Scroll"),
            new KeyValuePair<int, string>(30030, "Grid"), new KeyValuePair<int, string>(30029, "GridItem"),
            new KeyValuePair<int, string>(30038, "Table"), new KeyValuePair<int, string>(30040, "Text"),
            new KeyValuePair<int, string>(30044, "Window"),
        };

        static readonly HashSet<string> Interactive = new HashSet<string>
        {
            "Button", "Edit", "ComboBox", "CheckBox", "RadioButton", "ListItem", "MenuItem", "TabItem", "DataItem",
            "Spinner", "Slider", "Hyperlink", "TreeItem", "SplitButton", "Document", "Table", "DataGrid", "List",
            "Tree", "Tab", "Menu", "MenuBar", "ToolBar",
        };

        // Parts of a composite control (a combo box's "Open" button, scroll bar arrows, title bar buttons) are
        // reached through their parent, so they are not graded on their own.
        static readonly HashSet<string> Composite = new HashSet<string> { "ComboBox", "ScrollBar", "TitleBar", "Spinner" };

        static int Main(string[] args)
        {
            Options options;
            try { options = Options.Parse(args); }
            catch (ArgumentException ex)
            {
                if (ex.Message.Length > 0) Console.Error.WriteLine("UiaReport: " + ex.Message);
                Console.Error.WriteLine("usage: UiaReport.exe -Exe PATH [-Arguments A] | -ProcessName N | -WindowTitle T " +
                                        "[-WaitSeconds S] [-MaxDepth D] [-Csv FILE] [-Json FILE] [-KeepOpen] [-Quiet]");
                return 2;
            }

            Process launched = null;
            try
            {
                int processId = 0;
                if (options.Exe != null)
                {
                    launched = Process.Start(new ProcessStartInfo(Path.GetFullPath(options.Exe), options.Arguments));
                    processId = launched.Id;
                }
                else if (options.ProcessName != null)
                {
                    var running = Process.GetProcessesByName(options.ProcessName).FirstOrDefault(p => p.MainWindowHandle != IntPtr.Zero);
                    if (running == null) throw new InvalidOperationException("process '" + options.ProcessName + "' has no window");
                    processId = running.Id;
                }
                IUIAutomationElement window = FindWindow(processId, options);
                Thread.Sleep(500);  // let the first dialog finish laying out

                var rows = new List<Row>();
                Walk(window, 0, "/Window", null, rows, options.MaxDepth);
                Report(window, rows, options);
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("UiaReport: " + ex.Message);
                return 1;
            }
            finally
            {
                if (launched != null && !options.KeepOpen && !launched.HasExited) launched.Kill();
            }
        }

        static IUIAutomationElement FindWindow(int processId, Options options)
        {
            var deadline = DateTime.UtcNow.AddSeconds(options.WaitSeconds);
            IUIAutomationCondition condition = processId != 0
                ? Uia.CreatePropertyCondition(ProcessIdProperty, processId)
                : Uia.CreateTrueCondition();
            while (DateTime.UtcNow < deadline)
            {
                var windows = Uia.GetRootElement().FindAll(TreeScope.TreeScope_Children, condition);
                for (int i = 0; i < windows.Length; i++)
                {
                    var window = windows.GetElement(i);
                    string name = window.CurrentName ?? "";
                    if (options.WindowTitle != null && name.IndexOf(options.WindowTitle, StringComparison.OrdinalIgnoreCase) < 0) continue;
                    return window;
                }
                Thread.Sleep(250);  // polling a GUI that is still starting; there is no event to wait on
            }
            throw new TimeoutException("no window found within " + options.WaitSeconds + " s");
        }

        static string TypeName(int id)
        {
            string name;
            return ControlTypes.TryGetValue(id, out name) ? name : id.ToString();
        }

        static string Patterns(IUIAutomationElement e)
        {
            var names = new List<string>();
            foreach (var pair in PatternProperties)
            {
                object available = e.GetCurrentPropertyValue(pair.Key);
                if (available is bool && (bool)available) names.Add(pair.Value);
            }
            return string.Join(",", names);
        }

        static string Value(IUIAutomationElement e, string patterns)
        {
            if (!patterns.Split(',').Contains("Value")) return "";
            object password = e.GetCurrentPropertyValue(IsPasswordProperty);
            if (password is bool && (bool)password) return "***";
            var pattern = e.GetCurrentPattern(ValuePattern) as IUIAutomationValuePattern;
            return pattern == null ? "" : pattern.CurrentValue ?? "";
        }

        static string Grade(string type, string id, string name, string className, string parentType)
        {
            if (!Interactive.Contains(type) || (parentType != null && Composite.Contains(parentType))) return "";
            bool handleId = Regex.IsMatch(id, @"^\d+$") && className.StartsWith("WindowsForms10", StringComparison.Ordinal);
            if (id.Length > 0 && !handleId) return "good";
            return name.Length > 0 ? "fair" : "poor";
        }

        static void Walk(IUIAutomationElement e, int depth, string path, string parentType, List<Row> rows, int maxDepth)
        {
            if (depth > maxDepth) return;
            var walker = Uia.ControlViewWalker;
            var children = new List<IUIAutomationElement>();
            for (var child = walker.GetFirstChildElement(e); child != null; child = walker.GetNextSiblingElement(child))
                children.Add(child);

            string type = TypeName(e.CurrentControlType);
            string id = e.CurrentAutomationId ?? "", name = e.CurrentName ?? "", className = e.CurrentClassName ?? "";
            string patterns = Patterns(e);
            var row = new Row
            {
                Depth = depth, ControlType = type, AutomationId = id, Name = name, ClassName = className,
                Enabled = e.CurrentIsEnabled != 0, Children = children.Count, Patterns = patterns,
                Value = Value(e, patterns), Grade = Grade(type, id, name, className, parentType), Path = path,
            };
            if (row.Grade.Length == 0 && type == "Pane" && children.Count == 0 && name.Length == 0) row.Grade = "opaque";
            rows.Add(row);

            var seen = new Dictionary<string, int>();
            foreach (var child in children)
            {
                string childType = TypeName(child.CurrentControlType);
                int n;
                seen.TryGetValue(childType, out n);
                seen[childType] = ++n;
                Walk(child, depth + 1, path + "/" + childType + "[" + n + "]", type, rows, maxDepth);
            }
        }

        static void Report(IUIAutomationElement window, List<Row> rows, Options options)
        {
            if (!options.Quiet)
            {
                foreach (var r in rows)
                {
                    string grade = r.Grade.Length > 0 ? "[" + r.Grade + "]" : "";
                    string extra = (r.Patterns.Length > 0 ? " patterns=" + r.Patterns : "") +
                                   (r.Value.Length > 0 ? " value='" + r.Value + "'" : "");
                    Console.WriteLine("{0}{1,-8} {2}  id='{3}' name='{4}' class='{5}'{6}", new string(' ', r.Depth * 2), grade,
                                      r.ControlType, r.AutomationId, r.Name, r.ClassName, extra);
                }
            }
            var graded = rows.Where(r => r.Grade == "good" || r.Grade == "fair" || r.Grade == "poor").ToList();
            int good = graded.Count(r => r.Grade == "good"), fair = graded.Count(r => r.Grade == "fair");
            int poor = graded.Count(r => r.Grade == "poor"), opaque = rows.Count(r => r.Grade == "opaque");
            int readiness = (int)Math.Round(100.0 * (good + 0.5 * fair) / Math.Max(1, graded.Count));
            Console.WriteLine();
            Console.WriteLine("Native UIA3 view - UI Automation readiness: {0}%  ({1} interactive: {2} good, {3} name only, " +
                              "{4} unlocatable; {5} opaque pane(s))", readiness, graded.Count, good, fair, poor, opaque);

            if (options.Csv != null) WriteCsv(options.Csv, rows);
            if (options.Json != null)
            {
                var summary = new Dictionary<string, object>
                {
                    {"view", "native-uia3"}, {"window", window.CurrentName}, {"elements", rows.Count},
                    {"interactive", graded.Count}, {"good_automation_id", good}, {"fair_name_only", fair},
                    {"poor_unlocatable", poor}, {"opaque_panes", opaque}, {"readiness_percent", readiness},
                };
                var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
                Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(options.Json)));
                File.WriteAllText(options.Json, serializer.Serialize(new Dictionary<string, object> { {"summary", summary}, {"elements", rows} }),
                                  new UTF8Encoding(false));
            }
        }

        static void WriteCsv(string file, List<Row> rows)
        {
            Func<object, string> cell = v =>
            {
                string s = Convert.ToString(v) ?? "";
                return s.IndexOfAny(new[] { ',', '"', '\n', '\r' }) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;
            };
            var lines = new List<string> { "Depth,ControlType,AutomationId,Name,ClassName,Enabled,Children,Patterns,Value,Grade,Path" };
            lines.AddRange(rows.Select(r => string.Join(",", new[]
            {
                cell(r.Depth), cell(r.ControlType), cell(r.AutomationId), cell(r.Name), cell(r.ClassName), cell(r.Enabled),
                cell(r.Children), cell(r.Patterns), cell(r.Value), cell(r.Grade), cell(r.Path),
            })));
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(file)));
            File.WriteAllLines(file, lines, new UTF8Encoding(false));
        }
    }
}
