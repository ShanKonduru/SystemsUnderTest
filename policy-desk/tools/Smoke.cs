// PolicyDeskSmoke - drives Harbor Mutual Policy Desk end to end through native UI Automation (UIA3), in one release.
//
// It proves the app can be automated the way WinAppDriver, the Appium Windows driver or FlaUI would do it, and that
// the locator map the app writes (data\locator-map.json) is right: every control is found by the AutomationId the map
// gives for the release. It is also a worked example for generated scripts:
//   - wait for a state (underwriting result, status text), never a fixed sleep
//   - click anything that opens a modal dialog with the mouse: the UIA Invoke pattern blocks until the dialog closes
//   - reach the icon-only toolbar buttons by position, and v4 link labels through their Hyperlink child
//   - check the outcome in the system of record (data\policydesk.json), not only on screen
//
//   tools\smoke.bat [v1|v2|v3|v4]       (builds the app and this tool first; needs an unlocked desktop)
//
// Not run in CI: it needs an interactive desktop. Exit code 0 = passed.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Interop.UIAutomationClient;

namespace PolicyDeskSmoke
{
    class StepFailed : Exception
    {
        public StepFailed(string message) : base(message) { }
    }

    static class Program
    {
        static readonly IUIAutomation Uia = new CUIAutomation();
        const int ProcessIdProperty = 30002, AutomationIdProperty = 30011, NameProperty = 30005, ControlTypeProperty = 30003;
        const int InvokePattern = 10000, ValuePattern = 10002, SelectionItemPattern = 10010, WindowPattern = 10009;
        const int HyperlinkType = 50005, TextType = 50020, TabItemType = 50019, WindowType = 50032, ButtonType = 50000;

        static Dictionary<string, Dictionary<string, object>> map;
        static int pid;
        static string dataDir;
        static int step;

        [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
        [DllImport("user32.dll")] static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);

        static int Main(string[] args)
        {
            string release = args.Length > 0 ? args[0] : "v1";
            string exe = Path.GetFullPath(args.Length > 1 ? args[1] : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, @"..\..\app\bin\PolicyDesk.exe"));
            dataDir = Path.Combine(Path.GetTempPath(), "policydesk-smoke-" + release);
            if (Directory.Exists(dataDir)) Directory.Delete(dataDir, true);
            Directory.CreateDirectory(dataDir);
            string arguments = "--reset --release " + release + " --release-seed 7 --latency-ms 600 --data \"" + dataDir + "\"";
            Console.WriteLine("Policy Desk smoke, release " + release + ": " + exe + " " + arguments);
            var process = Process.Start(new ProcessStartInfo(exe, arguments));
            pid = process.Id;
            try
            {
                Run();
                Console.WriteLine("PASSED (" + step + " steps)");
                return 0;
            }
            catch (StepFailed ex)
            {
                Console.WriteLine("FAILED at step " + step + ": " + ex.Message);
                return 1;
            }
            finally
            {
                if (!process.WaitForExit(5000)) process.Kill();
            }
        }

        static void Run()
        {
            Step("the app writes its locator map");
            Until(() => File.Exists(Path.Combine(dataDir, "locator-map.json")), "data\\locator-map.json");
            var root = (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(File.ReadAllText(Path.Combine(dataDir, "locator-map.json")));
            map = ((object[])root["elements"]).Cast<Dictionary<string, object>>().ToDictionary(e => (string)e["key"]);

            Step("the sign-in dialog opens over the main window");
            var main = Until(() => TopWindow("MainForm"), "main window");
            var login = Until(() => Find(main, "LoginForm"), "sign-in dialog");

            Step("a wrong password is refused");
            SetText(login, "txtUser", "smoke");
            SetText(login, "txtPassword", "wrong");
            Press(login, "btnSignIn", false);
            Until(() => Find(login, "lblError").CurrentName == "Invalid user name or password.", "the error message");

            Step("the right password signs in");
            SetText(login, "txtPassword", "harbor");
            Press(login, "btnSignIn", false);
            Until(() => Find(main, "LoginForm", false) == null, "the sign-in dialog to close");
            Until(() => StatusTexts(main).Any(t => t == "User: smoke (Boston)"), "the signed-in user in the status bar");

            Step("an Umbrella quote for a customer with a bound Auto policy is approved after the wait");
            SetText(main, "txtPolicyholder", "Dana Whitfield");
            SetText(main, "txtEmail", "dana.whitfield@example.com");
            Choose(main, "cboProduct", "Umbrella");
            Press(main, "btnCalculate", false);
            Until(() => Find(main, "lblPremium").CurrentName == "Premium: 387.50 a year", "the premium 387.50");
            Press(main, "btnCheck", false);
            Until(() => Find(main, "lblUnderwriting").CurrentName == "Underwriting: Approved", "underwriting Approved");

            Step("Save quote stores quote 1006 as Pending");
            Press(main, "btnSave", false);
            Until(() => StatusTexts(main).Any(t => t == "Quote 1006 saved (Pending)"), "the saved status");
            var saved = Quote(1006);
            Expect(saved != null && (string)saved["status"] == "Pending" && (string)saved["product"] == "Umbrella"
                   && Convert.ToDecimal(saved["premium"]) == 387.50m, "policydesk.json has quote 1006, Umbrella, Pending, 387.50");

            Step("the icon-only Save button (second toolbar button) reaches a message box");
            var toolbar = Find(main, "toolbarMain");
            var buttons = toolbar.FindAll(TreeScope.TreeScope_Children, Uia.CreatePropertyCondition(ControlTypeProperty, ButtonType));
            Expect(buttons.Length == 4, "four toolbar buttons");
            Click(buttons.GetElement(1));
            var box = Until(() => Dialog(main, "Policy Desk"), "the message box");
            Expect(Texts(box).Contains("Policyholder is required."), "message: Policyholder is required.");
            Invoke(box.FindFirst(TreeScope.TreeScope_Descendants, Uia.CreatePropertyCondition(NameProperty, "OK")));
            Until(() => Dialog(main, "Policy Desk") == null, "the message box to close");

            Step("open quote 1002 from the Quotes tab and bind it");
            SelectTab(main, "tabQuotes");
            var grid = Until(() => Find(main, "dgvQuotes"), "the quotes grid");
            var cell = Until(() => Cells(grid).FirstOrDefault(c => Value(c) == "1002"), "the cell holding 1002");
            Click(cell);
            Press(main, "btnOpenQuote", true);
            var details = Until(() => Find(main, "QuoteDetailsForm"), "the quote details dialog");
            Expect(Value(Find(details, "txtDetailStatus")) == "Pending", "quote 1002 is Pending");
            Press(details, "btnBind", false);
            Until(() => StatusTexts(main).Any(t => t == "Quote 1002 bound as policy HMP-1002"), "the bound status");
            Expect((string)Quote(1002)["status"] == "Bound" && (string)Quote(1002)["policy_number"] == "HMP-1002",
                   "policydesk.json has 1002 Bound as HMP-1002");

            Step("the activity log recorded it all");
            var kinds = Activity().Select(a => (string)a["kind"]).ToList();
            foreach (var kind in new[] { "reset", "launch", "sign_in_failed", "sign_in", "underwriting_checked", "quote_saved", "quote_bound" })
                Expect(kinds.Contains(kind), "activity has " + kind);

            Step("closing the window signs out");
            ((IUIAutomationWindowPattern)main.GetCurrentPattern(WindowPattern)).Close();
            Until(() => Activity().Any(a => (string)a["kind"] == "sign_out"), "sign_out in the activity log");
        }

        // ── finding ──────────────────────────────────────────────────────────
        static string Id(string key)
        {
            Dictionary<string, object> e;
            if (!map.TryGetValue(key, out e)) throw new StepFailed("no element " + key + " in the locator map");
            return (string)e["automation_id"];
        }

        static IUIAutomationElement TopWindow(string key)
        {
            var condition = Uia.CreateAndCondition(Uia.CreatePropertyCondition(ProcessIdProperty, pid),
                                                   Uia.CreatePropertyCondition(AutomationIdProperty, Id(key)));
            return Uia.GetRootElement().FindFirst(TreeScope.TreeScope_Children, condition);
        }

        static IUIAutomationElement Find(IUIAutomationElement scope, string key) { return Find(scope, key, true); }

        static IUIAutomationElement Find(IUIAutomationElement scope, string key, bool required)
        {
            var element = scope.FindFirst(TreeScope.TreeScope_Descendants, Uia.CreatePropertyCondition(AutomationIdProperty, Id(key)));
            if (element == null && required) throw new StepFailed("not found: " + key + " (AutomationId '" + Id(key) + "')");
            return element;
        }

        static IUIAutomationElement Dialog(IUIAutomationElement main, string title)
        {
            var condition = Uia.CreateAndCondition(Uia.CreatePropertyCondition(ControlTypeProperty, WindowType),
                                                   Uia.CreatePropertyCondition(NameProperty, title));
            return main.FindFirst(TreeScope.TreeScope_Descendants, condition);
        }

        static List<string> Texts(IUIAutomationElement scope)
        {
            var all = scope.FindAll(TreeScope.TreeScope_Descendants, Uia.CreatePropertyCondition(ControlTypeProperty, TextType));
            var names = new List<string>();
            for (int i = 0; i < all.Length; i++) names.Add(all.GetElement(i).CurrentName);
            return names;
        }

        // Status bar texts have no AutomationId: read them through the status bar.
        static List<string> StatusTexts(IUIAutomationElement main) { return Texts(Find(main, "statusMain")); }

        static IEnumerable<IUIAutomationElement> Cells(IUIAutomationElement grid)
        {
            var all = grid.FindAll(TreeScope.TreeScope_Descendants, Uia.CreateTrueCondition());
            for (int i = 0; i < all.Length; i++) yield return all.GetElement(i);
        }

        static string Value(IUIAutomationElement e)
        {
            var pattern = e.GetCurrentPattern(ValuePattern) as IUIAutomationValuePattern;
            return pattern == null ? null : pattern.CurrentValue;
        }

        // ── acting ───────────────────────────────────────────────────────────
        static void SetText(IUIAutomationElement scope, string key, string text)
        {
            ((IUIAutomationValuePattern)Find(scope, key).GetCurrentPattern(ValuePattern)).SetValue(text);
        }

        // A drop-down list fires its change events only for user input, so choose with the keyboard.
        static void Choose(IUIAutomationElement scope, string key, string item)
        {
            var box = Find(scope, key);
            box.SetFocus();
            SendKeys.SendWait("{HOME}");
            for (int i = 0; i < 20 && Value(box) != item; i++) SendKeys.SendWait("{DOWN}");
            Expect(Value(box) == item, key + " shows " + item);
        }

        static void SelectTab(IUIAutomationElement main, string key)
        {
            var condition = Uia.CreateAndCondition(Uia.CreatePropertyCondition(ControlTypeProperty, TabItemType),
                                                   Uia.CreatePropertyCondition(NameProperty, (string)map[key]["name"]));
            var tab = Until(() => main.FindFirst(TreeScope.TreeScope_Descendants, condition), "tab " + map[key]["name"]);
            ((IUIAutomationSelectionItemPattern)tab.GetCurrentPattern(SelectionItemPattern)).Select();
        }

        // A button, or in v4 a link label: a Text element whose Hyperlink child is what to click.
        // Anything that opens a modal dialog is clicked with the mouse (Invoke would block until it closes).
        static void Press(IUIAutomationElement scope, string key, bool opensModal)
        {
            var element = Find(scope, key);
            if (element.CurrentControlType == TextType)
                element = element.FindFirst(TreeScope.TreeScope_Children, Uia.CreatePropertyCondition(ControlTypeProperty, HyperlinkType));
            Until(() => element.CurrentIsEnabled != 0, key + " to be enabled");
            if (opensModal) Click(element);
            else Invoke(element);
        }

        static void Invoke(IUIAutomationElement element)
        {
            ((IUIAutomationInvokePattern)element.GetCurrentPattern(InvokePattern)).Invoke();
        }

        static void Click(IUIAutomationElement element)
        {
            var main = TopWindow("MainForm");
            SetForegroundWindow(main.CurrentNativeWindowHandle);
            var r = element.CurrentBoundingRectangle;
            SetCursorPos((r.left + r.right) / 2, (r.top + r.bottom) / 2);
            mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero);   // left down
            mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero);   // left up
        }

        // ── the system of record ─────────────────────────────────────────────
        static Dictionary<string, object> Store()
        {
            for (int attempt = 0; ; attempt++)
            {
                try { return (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(File.ReadAllText(Path.Combine(dataDir, "policydesk.json"))); }
                catch (IOException) { if (attempt > 20) throw; Thread.Sleep(50); }
            }
        }

        static Dictionary<string, object> Quote(int number)
        {
            return ((object[])Store()["quotes"]).Cast<Dictionary<string, object>>().FirstOrDefault(q => Convert.ToInt32(q["number"]) == number);
        }

        static IEnumerable<Dictionary<string, object>> Activity()
        {
            return ((object[])Store()["activity"]).Cast<Dictionary<string, object>>();
        }

        // ── waiting and checking ─────────────────────────────────────────────
        static void Step(string what)
        {
            step++;
            Console.WriteLine("  " + step + ". " + what);
        }

        static void Expect(bool ok, string what)
        {
            if (!ok) throw new StepFailed("expected " + what);
        }

        static T Until<T>(Func<T> probe, string what) where T : class
        {
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (true)
            {
                try
                {
                    var result = probe();
                    if (result != null) return result;
                }
                catch (StepFailed) { if (DateTime.UtcNow > deadline) throw; }
                catch (COMException) { }
                if (DateTime.UtcNow > deadline) throw new StepFailed("timed out waiting for " + what);
                Thread.Sleep(100);
            }
        }

        static void Until(Func<bool> probe, string what)
        {
            Until(() => probe() ? "ok" : null, what);
        }
    }
}
