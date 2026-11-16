// UI releases, live drift and the locator map: what makes recorded locators break so self-healing can be shown.
//
// Every screen is written once, as release v1, and builds its controls through ReleaseUi. ReleaseUi applies the
// selected release's rules from releases.json (copied next to the exe) as each control is made:
//   - names:  the control's Name, which UI Automation reports as its AutomationId (v2 renames, v3/v4 random)
//   - text:   captions, labels, button and menu texts, column headers, window titles
//   - types:  a Button becomes a LinkLabel (UI Automation sees a Hyperlink instead of a Button)
//   - swaps:  two form rows trade places, so position-based healing picks the wrong field
// Live drift (--drift N) renames every control again every N seconds while the app runs.
//
// releases.json is the only place release rules live: the screens and the locator map both read it.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;

namespace PolicyDesk
{
    class ReleaseRules
    {
        public List<string> Releases = new List<string>();
        public Dictionary<string, string> Schemes = new Dictionary<string, string>();
        public Dictionary<string, Dictionary<string, string>> Names = new Dictionary<string, Dictionary<string, string>>();
        public Dictionary<string, Dictionary<string, string>> Text = new Dictionary<string, Dictionary<string, string>>();
        public Dictionary<string, Dictionary<string, string>> Types = new Dictionary<string, Dictionary<string, string>>();
        public Dictionary<string, List<string[]>> Swaps = new Dictionary<string, List<string[]>>();
        public Dictionary<string, string> Descriptions = new Dictionary<string, string>();

        public static string DefaultPath { get { return Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "releases.json"); } }

        public static ReleaseRules Load(string path)
        {
            if (!File.Exists(path)) throw new FileNotFoundException("Release rules not found: " + path + " (rebuild with app\\build.bat).");
            var root = (Dictionary<string, object>)Json.Parse(File.ReadAllText(path, Encoding.UTF8));
            var rules = new ReleaseRules();
            rules.Releases.AddRange(Json.Arr(root, "releases").Select(x => (string)x));
            rules.Schemes = Flat(Json.Obj(root, "schemes"));
            rules.Descriptions = Flat(Json.Obj(root, "descriptions"));
            rules.Names = Nested(Json.Obj(root, "names"));
            rules.Text = Nested(Json.Obj(root, "text"));
            rules.Types = Nested(Json.Obj(root, "types"));
            var swaps = Json.Obj(root, "swaps") ?? new Dictionary<string, object>();
            foreach (var pair in swaps)
                rules.Swaps[pair.Key] = ((object[])pair.Value).Select(p => ((object[])p).Select(x => (string)x).ToArray()).ToList();
            foreach (string release in rules.Releases)
                if (!rules.Schemes.ContainsKey(release)) throw new InvalidDataException("releases.json: no scheme for " + release);
            return rules;
        }

        static Dictionary<string, string> Flat(Dictionary<string, object> d)
        {
            var result = new Dictionary<string, string>();
            if (d != null) foreach (var pair in d) result[pair.Key] = (string)pair.Value;
            return result;
        }

        static Dictionary<string, Dictionary<string, string>> Nested(Dictionary<string, object> d)
        {
            var result = new Dictionary<string, Dictionary<string, string>>();
            if (d != null) foreach (var pair in d) result[pair.Key] = Flat((Dictionary<string, object>)pair.Value);
            return result;
        }

        public string Scheme(string release) { return Schemes[release]; }

        public static string Lookup(Dictionary<string, Dictionary<string, string>> maps, string release, string key)
        {
            Dictionary<string, string> map;
            string value;
            if (maps.TryGetValue(release, out map) && map.TryGetValue(key, out value)) return value;
            return null;
        }
    }

    // One control the release layer made, keyed by its authored (v1) name.
    class UiEntry
    {
        public string Key;
        public string Window;
        public object Target;        // Control or ToolStripItem
        public string ControlType;   // the UI Automation control type a driver should see
        public string UiaName;       // the UI Automation Name a driver should see
        public bool Legacy;          // deliberately unnamed: only position, tooltip or image can reach it
        public string Note = "";
    }

    // Random names are shared by every ReleaseUi of one launch, so a dialog opened later, or a locator map built
    // on the side, sees the same names as the live window.
    class NameState
    {
        public Random Rng;
        public bool Seeded;
        public int Generation;
        public readonly Dictionary<string, string> Random = new Dictionary<string, string>();
        public readonly HashSet<string> Used = new HashSet<string>();
        public readonly HashSet<string> AskedTexts = new HashSet<string>();   // for the self-test: every text rule must be used
    }

    class ReleaseUi
    {
        public readonly ReleaseRules Rules;
        public readonly string Release;
        public readonly NameState State;
        public readonly List<UiEntry> Entries = new List<UiEntry>();
        readonly Dictionary<string, Control[]> rows = new Dictionary<string, Control[]>();

        public ReleaseUi(ReleaseRules rules, string release, int seed)
            : this(rules, release, new NameState { Rng = seed >= 0 ? new Random(seed) : new Random(), Seeded = seed >= 0 })
        {
        }

        ReleaseUi(ReleaseRules rules, string release, NameState state)
        {
            if (!rules.Releases.Contains(release)) throw new ArgumentException("Unknown release: " + release);
            Rules = rules;
            Release = release;
            State = state;
        }

        // A ReleaseUi that shares this one's names but keeps its own list of entries (for building locator maps).
        public ReleaseUi Sibling() { return new ReleaseUi(Rules, Release, State); }

        public bool RandomNames { get { return State.Generation > 0 || Rules.Scheme(Release) == "random"; } }

        // True when names cannot be known before launch: random per load (no --release-seed) or drifting.
        public bool Dynamic { get { return State.Generation > 0 || (Rules.Scheme(Release) == "random" && !State.Seeded); } }

        public string NameOf(string key)
        {
            if (RandomNames)
            {
                string name;
                if (!State.Random.TryGetValue(key, out name))
                {
                    do { name = "c" + State.Rng.Next().ToString("x8"); } while (!State.Used.Add(name));
                    State.Random[key] = name;
                }
                return name;
            }
            if (Rules.Scheme(Release) == "renamed") return ReleaseRules.Lookup(Rules.Names, Release, key) ?? key;
            return key;
        }

        public string T(string text)
        {
            State.AskedTexts.Add(text);
            return ReleaseRules.Lookup(Rules.Text, Release, text) ?? text;
        }

        public string TypeOf(string key) { return ReleaseRules.Lookup(Rules.Types, Release, key) ?? ""; }

        // Control types whose UI Automation Name is their own text (others take it from a label, or have none).
        static readonly HashSet<string> NamedByText = new HashSet<string> { "Window", "Button", "Hyperlink", "CheckBox", "RadioButton", "Group", "TabItem" };

        public T Reg<T>(T control, string window, string key, string controlType, string uiaName) where T : Control
        {
            control.Name = NameOf(key);
            if (uiaName == null) uiaName = NamedByText.Contains(controlType) ? Fmt.StripMnemonic(control.Text) : "";
            Entries.Add(new UiEntry { Key = key, Window = window, Target = control, ControlType = controlType, UiaName = uiaName });
            return control;
        }

        // Adds a note for the locator map to the entry registered last.
        public void Note(string note) { Entries[Entries.Count - 1].Note = note; }

        // A legacy control: listed in the locator map, but given no name, the way old apps often leave them.
        public void Legacy(object target, string window, string key, string controlType, string note)
        {
            Entries.Add(new UiEntry { Key = key, Window = window, Target = target, ControlType = controlType, Legacy = true, Note = note });
        }

        // A push button, or in releases whose "types" say so, a LinkLabel that does the same thing.
        public Control Button(string window, string key, string text, EventHandler onClick)
        {
            text = T(text);
            if (TypeOf(key) == "link")
            {
                var link = new LinkLabel { Text = Fmt.StripMnemonic(text), AutoSize = false, TextAlign = ContentAlignment.MiddleCenter };
                link.LinkClicked += delegate(object sender, LinkLabelLinkClickedEventArgs e) { onClick(sender, EventArgs.Empty); };
                Reg(link, window, key, "Text", Fmt.StripMnemonic(text));
                Note("Was a Button; now a link label: this Text element carries the AutomationId, and its Hyperlink child "
                     + "(Invoke pattern, no AutomationId) is what to click.");
                return link;
            }
            var button = new Button { Text = text, UseVisualStyleBackColor = true };
            button.Click += onClick;
            return Reg(button, window, key, "Button", Fmt.StripMnemonic(text));
        }

        public ToolStripMenuItem Menu(string window, string key, string text, EventHandler onClick, Keys shortcut)
        {
            var item = new ToolStripMenuItem(T(text), null, onClick) { Name = NameOf(key) };
            if (shortcut != Keys.None) item.ShortcutKeys = shortcut;
            Entries.Add(new UiEntry { Key = key, Window = window, Target = item, ControlType = "MenuItem", UiaName = Fmt.StripMnemonic(item.Text) });
            return item;
        }

        // A caption label and its input on one row. The label comes just before the input in tab order, which is how
        // UI Automation gives the input its Name. Rows can be swapped by the release's "swaps" rules.
        public Label Field(Control parent, string window, string caption, Control input, string key, string controlType,
                           int x, int top, int labelWidth, int inputWidth, int tabIndex)
        {
            caption = T(caption);
            var label = new Label { Text = caption, AutoSize = true, TabIndex = tabIndex, Location = new Point(x, top + 3) };
            input.TabIndex = tabIndex + 1;
            input.SetBounds(x + labelWidth, top, inputWidth, input is ComboBox ? input.Height : 24);
            Reg(input, window, key, controlType, Fmt.StripMnemonic(caption));
            parent.Controls.Add(label);
            parent.Controls.Add(input);
            rows[key] = new Control[] { label, input };
            return label;
        }

        // A control without a caption label (check box, radio button) that can still take part in a swap.
        public T Row<T>(T control, string key) where T : Control
        {
            rows[key] = new Control[] { null, control };
            return control;
        }

        public void ApplySwaps()
        {
            List<string[]> swaps;
            if (!Rules.Swaps.TryGetValue(Release, out swaps)) return;
            foreach (var pair in swaps)
            {
                Control[] a, b;
                if (!rows.TryGetValue(pair[0], out a) || !rows.TryGetValue(pair[1], out b)) continue;
                for (int i = 0; i < 2; i++)
                {
                    if (a[i] == null || b[i] == null) continue;
                    var location = a[i].Location;
                    a[i].Location = b[i].Location;
                    b[i].Location = location;
                }
            }
        }

        public string SwappedWith(string key)
        {
            List<string[]> swaps;
            if (!Rules.Swaps.TryGetValue(Release, out swaps)) return null;
            foreach (var pair in swaps)
            {
                if (pair[0] == key) return pair[1];
                if (pair[1] == key) return pair[0];
            }
            return null;
        }

        // Live drift: a new random name for every control, including the ones already on screen.
        public void Drift()
        {
            State.Generation++;
            Entries.RemoveAll(e => (e.Target is Control && ((Control)e.Target).IsDisposed)
                                   || (e.Target is ToolStripItem && ((ToolStripItem)e.Target).IsDisposed));   // closed dialogs
            State.Random.Clear();
            foreach (var entry in Entries)
            {
                if (entry.Legacy) continue;
                var control = entry.Target as Control;
                if (control != null && !control.IsDisposed) control.Name = NameOf(entry.Key);
                var item = entry.Target as ToolStripItem;
                if (item != null && !item.IsDisposed) item.Name = NameOf(entry.Key);
            }
        }
    }

    // Ground truth for scoring heals: every authored element of every window, with what a driver sees in this
    // release. A framework under test must not use it to heal.
    static class LocatorMap
    {
        public const string LiveFileName = "locator-map.json";

        public static JObj Build(ReleaseUi ui, Options options)
        {
            var side = ui.Sibling();
            var store = Store.Seed(options.Today);
            var sample = store.Find(1002);
            var forms = new List<Form>
            {
                new LoginForm(side, options, store),
                new MainForm(side, options, store, false),
                new QuoteDetailsForm(side, store, sample, "map"),
                new DeclineForm(side),
                new OptionsForm(side, store, "map"),
                new ProductGuideForm(side),
            };
            var elements = side.Entries.Select(e =>
            {
                var control = e.Target as Control;
                var item = e.Target as ToolStripItem;
                // Menu items and status bar texts are not windows: UI Automation gives them no AutomationId.
                string id = e.Legacy || control == null ? "" : control.Name;
                var element = new JObj
                {
                    { "key", e.Key }, { "window", e.Window }, { "automation_id", id }, { "control_type", e.ControlType },
                    { "name", e.UiaName }, { "legacy", e.Legacy },
                };
                string note = e.Note;
                if (item != null && note.Length == 0) note = "Not a window, so no AutomationId: match it by name.";
                if (note.Length > 0) element.Add("note", note);
                string swapped = side.SwappedWith(e.Key);
                if (swapped != null) element.Add("swapped_with", swapped);
                return (object)element;
            }).ToList();
            foreach (var form in forms) form.Dispose();
            string description;
            ui.Rules.Descriptions.TryGetValue(ui.Release, out description);
            return new JObj
            {
                { "release", ui.Release },
                { "description", description ?? "" },
                { "scheme", ui.Rules.Scheme(ui.Release) },
                { "dynamic", ui.Dynamic },
                { "drift_generation", ui.State.Generation },
                { "release_seed", options.ReleaseSeed >= 0 ? (object)options.ReleaseSeed : null },
                { "elements", elements },
            };
        }

        public static void WriteLive(ReleaseUi ui, Options options, string dir)
        {
            string path = Path.Combine(dir, LiveFileName), temp = path + ".tmp";
            File.WriteAllText(temp, Json.Write(Build(ui, options)), new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(temp, path, null);
            else File.Move(temp, path);
        }
    }
}
