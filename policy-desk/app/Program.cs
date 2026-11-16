// Harbor Mutual Policy Desk: entry point and launch options (test hooks).
//
// Two builds of the same sources (app\build.bat):
//   bin\PolicyDesk.exe      the desktop app (Windows GUI subsystem)
//   bin\policydesk-cli.exe  the same, as a console program: --self-test and --locator-map print to the console
//                           and the exit code says how it went, so scripts and CI can wait for them.
// Every option has an environment variable; the command line wins.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Harbor Mutual Policy Desk (system under test)")]
[assembly: AssemblyVersion("3.0.0.0")]

namespace PolicyDesk
{
    class OptionsException : Exception
    {
        public OptionsException(string message) : base(message) { }
    }

    class Options
    {
        public static readonly DateTime DefaultToday = new DateTime(2026, 11, 3);
        public DateTime Today = DefaultToday;
        public string DataDir = DefaultDataDir;
        public bool Reset;
        public string Release = "v1";
        public int ReleaseSeed = -1;
        public int DriftSeconds;
        public int LatencyMs = 800;
        public string Password = "harbor";
        public string ExportDir;
        public bool SelfTest;
        public string LocatorMapRelease;
        public string OutFile;
        public bool Help;
        public HashSet<string> Defects = new HashSet<string>();

        public const string Usage =
            "PolicyDesk.exe [options]\n" +
            "  --reset                 start from the seed data (5 quotes) and an empty activity log\n" +
            "  --today YYYY-MM-DD      fixed \"today\"                         POLICYDESK_TODAY (2026-11-03)\n" +
            "  --data DIR              system of record folder               POLICYDESK_DATA (policy-desk\\data)\n" +
            "  --release v1|v2|v3|v4   UI release                            POLICYDESK_RELEASE (v1)\n" +
            "  --release-seed N        make v3/v4 random names repeatable    POLICYDESK_RELEASE_SEED\n" +
            "  --drift N               rename every control every N s        POLICYDESK_DRIFT_SECONDS (0 = off, else 2-300)\n" +
            "  --latency-ms N          underwriting check delay              POLICYDESK_LATENCY_MS (800, 0-30000)\n" +
            "  --password TEXT         sign-in password                      POLICYDESK_PASSWORD (harbor)\n" +
            "  --export-dir DIR        folder the Export dialog opens in     POLICYDESK_EXPORT_DIR\n" +
            "  --defects a,b           test runs only: planted defects       POLICYDESK_DEFECTS (see README)\n" +
            "  --locator-map RELEASE   write the locator map and exit (with --out FILE, or to the console)\n" +
            "  --self-test             run the built-in checks and exit (0 = all passed)\n" +
            "  --help                  show this text";

        static string DefaultDataDir
        {
            get { return Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "data")); }
        }

        public static Options Parse(string[] args, Func<string, string> env)
        {
            var o = new Options();
            ApplyEnv(o, env);
            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                switch (arg.ToLowerInvariant())
                {
                    case "--reset": o.Reset = true; break;
                    case "--self-test": o.SelfTest = true; break;
                    case "--help": case "-h": case "/?": o.Help = true; break;
                    case "--today": o.Today = ParseToday(Value(args, ref i)); break;
                    case "--data": o.DataDir = Path.GetFullPath(Value(args, ref i)); break;
                    case "--release": o.Release = ParseRelease(Value(args, ref i)); break;
                    case "--release-seed": o.ReleaseSeed = ParseInt(arg, Value(args, ref i), 0, int.MaxValue); break;
                    case "--drift": o.DriftSeconds = ParseDrift("--drift", Value(args, ref i)); break;
                    case "--latency-ms": o.LatencyMs = ParseInt(arg, Value(args, ref i), 0, 30000); break;
                    case "--password": o.Password = Value(args, ref i); break;
                    case "--export-dir": o.ExportDir = Path.GetFullPath(Value(args, ref i)); break;
                    case "--locator-map": o.LocatorMapRelease = ParseRelease(Value(args, ref i)); break;
                    case "--defects": o.Defects = PolicyDesk.Defects.Parse("--defects", Value(args, ref i)); break;
                    case "--out": o.OutFile = Path.GetFullPath(Value(args, ref i)); break;
                    default: throw new OptionsException("Unknown option: " + arg);
                }
            }
            if (o.Password.Length == 0) throw new OptionsException("The password must not be empty.");
            return o;
        }

        static void ApplyEnv(Options o, Func<string, string> env)
        {
            string v;
            if (Has(env, "POLICYDESK_TODAY", out v)) o.Today = ParseToday(v);
            if (Has(env, "POLICYDESK_DATA", out v)) o.DataDir = Path.GetFullPath(v);
            if (Has(env, "POLICYDESK_RELEASE", out v)) o.Release = ParseRelease(v);
            if (Has(env, "POLICYDESK_RELEASE_SEED", out v)) o.ReleaseSeed = ParseInt("POLICYDESK_RELEASE_SEED", v, 0, int.MaxValue);
            if (Has(env, "POLICYDESK_DRIFT_SECONDS", out v)) o.DriftSeconds = ParseDrift("POLICYDESK_DRIFT_SECONDS", v);
            if (Has(env, "POLICYDESK_LATENCY_MS", out v)) o.LatencyMs = ParseInt("POLICYDESK_LATENCY_MS", v, 0, 30000);
            if (Has(env, "POLICYDESK_PASSWORD", out v)) o.Password = v;
            if (Has(env, "POLICYDESK_EXPORT_DIR", out v)) o.ExportDir = Path.GetFullPath(v);
            if (Has(env, "POLICYDESK_DEFECTS", out v)) o.Defects = PolicyDesk.Defects.Parse("POLICYDESK_DEFECTS", v);
        }

        static bool Has(Func<string, string> env, string name, out string value)
        {
            value = env(name);
            return !string.IsNullOrEmpty(value);
        }

        static string Value(string[] args, ref int i)
        {
            if (i + 1 >= args.Length) throw new OptionsException(args[i] + " needs a value.");
            return args[++i];
        }

        static DateTime ParseToday(string text)
        {
            DateTime d;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out d))
                throw new OptionsException("Today must be a date like 2026-11-03, not \"" + text + "\".");
            return d;
        }

        static string ParseRelease(string text)
        {
            string r = text.Trim().ToLowerInvariant();
            if (r != "v1" && r != "v2" && r != "v3" && r != "v4") throw new OptionsException("Release must be v1, v2, v3 or v4, not \"" + text + "\".");
            return r;
        }

        static int ParseDrift(string what, string text)
        {
            int s = ParseInt(what, text, 0, 300);
            if (s == 1) throw new OptionsException(what + " must be 0 (off) or 2-300 seconds.");
            return s;
        }

        static int ParseInt(string what, string text, int min, int max)
        {
            int n;
            if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) || n < min || n > max)
                throw new OptionsException(what + " must be a whole number from " + min + " to " + max + ", not \"" + text + "\".");
            return n;
        }
    }

    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
#if CONSOLE
            bool console = true;
#else
            bool console = false;
#endif
            Options options;
            try
            {
                options = Options.Parse(args, Environment.GetEnvironmentVariable);
            }
            catch (OptionsException ex)
            {
                return Fail(console, ex.Message + "\n\n" + Options.Usage, 2);
            }
            if (options.Help) return Say(console, Options.Usage, 0);

            try
            {
                if (options.SelfTest)
                {
                    var report = new StringWriter();
                    int failures = SelfTest.Run(options, report);
                    return Say(console, report.ToString(), failures == 0 ? 0 : 1, options.OutFile);
                }
                var rules = ReleaseRules.Load(ReleaseRules.DefaultPath);
                if (options.LocatorMapRelease != null)
                {
                    var mapUi = new ReleaseUi(rules, options.LocatorMapRelease, options.ReleaseSeed);
                    return Say(console, Json.Write(LocatorMap.Build(mapUi, options)), 0, options.OutFile);
                }

                Defects.Set(options.Defects);   // only the app itself runs with them; the self-test and maps never do
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                var store = Store.Open(options.DataDir, options.Reset, options.Today);
                var ui = new ReleaseUi(rules, options.Release, options.ReleaseSeed);
                store.Log("system", "launch", "release " + options.Release + ", today " + Fmt.Date(options.Today)
                          + (options.Reset ? ", reset" : "") + (options.DriftSeconds > 0 ? ", drift " + options.DriftSeconds + "s" : ""));
                LocatorMap.WriteLive(ui, options, store.Dir);
                Application.Run(new MainForm(ui, options, store, true));
                return 0;
            }
            catch (Exception ex)
            {
                return Fail(console, ex.GetType().Name + ": " + ex.Message, 1);
            }
        }

        static int Say(bool console, string text, int code) { return Say(console, text, code, null); }

        static int Say(bool console, string text, int code, string outFile)
        {
            if (outFile != null)
            {
                File.WriteAllText(outFile, text, new UTF8Encoding(false));
                if (console) Console.WriteLine("Wrote " + outFile);
            }
            else if (console) Console.Write(text.EndsWith("\n") ? text : text + "\n");
            else MessageBox.Show(text, "Policy Desk", MessageBoxButtons.OK, code == 0 ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
            return code;
        }

        static int Fail(bool console, string text, int code)
        {
            if (console) Console.Error.WriteLine(text);
            else MessageBox.Show(text, "Policy Desk", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return code;
        }
    }
}
