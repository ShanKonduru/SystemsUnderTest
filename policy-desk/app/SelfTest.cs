// Built-in checks: policydesk-cli.exe --self-test (exit code 0 when all pass). CI runs them on Windows.
//
// They cover what a test engineer relies on without opening a window: prices, underwriting, validation, the store,
// launch options, the release rules and the locator map. The screens themselves are driven by UI automation
// (see docs\policy-desk-test-cases.csv).

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace PolicyDesk
{
    static class SelfTest
    {
        static TextWriter output;
        static int passed, failed;

        public static int Run(Options options, TextWriter writer)
        {
            output = writer;
            passed = failed = 0;
            Defects.Set(new string[0]);   // the checks describe the clean build
            var today = Options.DefaultToday;
            Section("pricing", delegate { PricingChecks(); });
            Section("underwriting", delegate { UnderwritingChecks(today); });
            Section("validation", delegate { ValidationChecks(today); });
            Section("store", delegate { StoreChecks(today); });
            Section("options", delegate { OptionChecks(); });
            Section("csv", delegate { CsvChecks(today); });
            Section("releases", delegate { ReleaseChecks(today); });
            Section("defects", delegate { DefectChecks(today); });
            output.WriteLine();
            output.WriteLine(failed == 0 ? "PASSED: " + passed + " checks" : "FAILED: " + failed + " of " + (passed + failed) + " checks");
            return failed;
        }

        static void Section(string name, Action body)
        {
            output.WriteLine("[" + name + "]");
            try { body(); }
            catch (Exception ex) { Check(false, name + " crashed", ex.GetType().Name + ": " + ex.Message); }
        }

        static void Check(bool ok, string what, string detail)
        {
            if (ok) passed++;
            else failed++;
            output.WriteLine((ok ? "  ok    " : "  FAIL  ") + what + (ok || string.IsNullOrEmpty(detail) ? "" : " -- " + detail));
        }

        static void Equal<T>(T expected, T actual, string what)
        {
            Check(EqualityComparer<T>.Default.Equals(expected, actual), what, "expected " + expected + ", got " + actual);
        }

        static QuoteInput Input(string product, string coverage, int deductible)
        {
            return new QuoteInput { Policyholder = "Test Person", Email = "t@example.com", Product = product, Coverage = coverage,
                                    Deductible = deductible, StartDate = Options.DefaultToday.AddDays(7) };
        }

        static void PricingChecks()
        {
            Equal(600.00m, Pricing.Calculate(Input("Auto", "Standard", 500)).Annual, "Auto Standard 500 = 600.00");
            Equal(198.00m, Pricing.Calculate(Input("Renters", "Basic", 250)).Annual, "Renters Basic 250 = 198.00");
            Equal(1619.20m, Pricing.Calculate(Input("Home", "Premium", 250)).Annual, "Home Premium 250 = 1,619.20");
            Equal(387.50m, Pricing.Calculate(Input("Umbrella", "Standard", 500)).Annual, "Umbrella Standard 500 = 387.50");
            var business = Input("Home", "Standard", 1000);
            business.CustomerType = "Business";
            Equal(1190.25m, Pricing.Calculate(business).Annual, "Business Home Standard 1000 = 1,190.25 (+15%)");
            var multi = Input("Auto", "Standard", 500);
            multi.MultiPolicy = true;
            Equal(540.00m, Pricing.Calculate(multi).Annual, "Multi-policy discount = 540.00 (-10%)");
            var roadside = Input("Auto", "Standard", 500);
            roadside.Roadside = true;
            Equal(645.00m, Pricing.Calculate(roadside).Annual, "Roadside on Auto adds 45.00");
            var roadsideHome = Input("Home", "Basic", 500);
            roadsideHome.Roadside = true;
            Equal(920.00m, Pricing.Calculate(roadsideHome).Annual, "Roadside is ignored for Home");
            var monthly = Input("Auto", "Standard", 500);
            monthly.PaymentPlan = "Monthly";
            var price = Pricing.Calculate(monthly);
            Equal(51.50m, price.Installment, "Monthly installment = 51.50 (600 x 1.03 / 12)");
            Equal(618.00m, price.PlanTotal, "Monthly plan total = 618.00");
            Equal(600.00m, Pricing.Calculate(Input("Auto", "Standard", 500)).PlanTotal, "Annual plan total = premium");
            var stacked = Input("Auto", "Premium", 250);
            stacked.CustomerType = "Business";
            stacked.MultiPolicy = true;
            stacked.Roadside = true;
            Equal(919.37m, Pricing.Calculate(stacked).Annual, "Every factor, rounded per step: 919.37");
        }

        static void UnderwritingChecks(DateTime today)
        {
            var store = Store.Seed(today);
            var umbrella = Input("Umbrella", "Standard", 500);
            umbrella.Policyholder = "Someone New";
            var uw = Pricing.Underwrite(umbrella, Pricing.Calculate(umbrella), store.Quotes);
            Equal("Referred", uw.Decision, "Umbrella without a bound Auto/Home is Referred");
            umbrella.Policyholder = "  dana WHITFIELD ";
            uw = Pricing.Underwrite(umbrella, Pricing.Calculate(umbrella), store.Quotes);
            Equal("Approved", uw.Decision, "Umbrella for Dana Whitfield (bound Auto 1001) is Approved, name match ignores case/spaces");
            var big = Input("Home", "Premium", 250);
            uw = Pricing.Underwrite(big, Pricing.Calculate(big), store.Quotes);
            Equal("Referred", uw.Decision, "Premium over 1,500.00 is Referred");
            var edge = Input("Home", "Premium", 500);
            uw = Pricing.Underwrite(edge, Pricing.Calculate(edge), store.Quotes);
            Equal("Approved", uw.Decision, "Home Premium 500 (1,472.00) is Approved");
        }

        static void ValidationChecks(DateTime today)
        {
            Func<QuoteInput, string> fields = i => string.Join(",", Pricing.Validate(i, today).Select(e => e.Field).ToArray());
            Equal("", fields(Input("Auto", "Standard", 500)), "A complete quote has no errors");
            var empty = new QuoteInput { StartDate = today };
            Equal("policyholder,contact", fields(empty), "Empty form: policyholder and contact");
            var bad = Input("Auto", "Standard", 500);
            bad.Email = "not-an-email";
            bad.Phone = "617555";
            Equal("email,phone", fields(bad), "Bad email and short phone");
            var phoneOnly = Input("Auto", "Standard", 500);
            phoneOnly.Email = "";
            phoneOnly.Phone = "6175550100";
            Equal("", fields(phoneOnly), "A phone number alone is enough contact");
            var past = Input("Auto", "Standard", 500);
            past.StartDate = today.AddDays(-1);
            Equal("start", fields(past), "Start date yesterday is rejected");
            var todayStart = Input("Auto", "Standard", 500);
            todayStart.StartDate = today;
            Equal("", fields(todayStart), "Start date today is allowed");
            var limit = Input("Auto", "Standard", 500);
            limit.StartDate = today.AddDays(60);
            Equal("", fields(limit), "Start date today + 60 is allowed");
            limit.StartDate = today.AddDays(61);
            Equal("start", fields(limit), "Start date today + 61 is rejected");
            var longName = Input("Auto", "Standard", 500);
            longName.Policyholder = new string('x', 61);
            Equal("policyholder", fields(longName), "Policyholder over 60 characters is rejected");
        }

        static void StoreChecks(DateTime today)
        {
            string dir = Path.Combine(Path.GetTempPath(), "policydesk-selftest-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = Store.Open(dir, false, today);
                Equal(5, store.Quotes.Count, "Seed has 5 quotes");
                Equal(1006, store.NextNumber, "New quotes are numbered from 1006");
                Equal("Bound,Pending,Pending,Referred,Declined", string.Join(",", store.Quotes.Select(q => q.Status).ToArray()), "Seed statuses");
                var input = Input("Auto", "Standard", 500);
                var added = store.AddQuote(input, "tester", "Boston");
                Equal(1006, added.Number, "First saved quote is 1006");
                Equal("Pending", added.Status, "An approved quote saves as Pending");
                store.Bind(store.Find(1002), "tester");
                Equal("HMP-1002", store.Find(1002).PolicyNumber, "Binding gives policy number HMP-<quote>");
                store.Decline(store.Find(1003), "Other: test", "tester");
                var reopened = Store.Open(dir, false, today);
                Equal(1007, reopened.NextNumber, "Next number survives a restart");
                Equal(6, reopened.Quotes.Count, "Saved quote survives a restart");
                Equal("Bound", reopened.Find(1002).Status, "Bind survives a restart");
                Equal("Other: test", reopened.Find(1003).DeclineReason, "Decline reason survives a restart");
                Equal(600.00m, reopened.Find(1006).Price.Annual, "Premium survives a restart");
                Equal(store.Activity.Count, reopened.Activity.Count, "Activity survives a restart");
                bool threw = false;
                try { reopened.Bind(reopened.Find(1003), "tester"); } catch (InvalidOperationException) { threw = true; }
                Check(threw, "A Declined quote cannot be bound", "");
                var reset = Store.Open(dir, true, today);
                Equal(5, reset.Quotes.Count, "--reset brings back the seed");
                Equal(1, reset.Activity.Count, "--reset clears the activity log");
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }

        static void OptionChecks()
        {
            Func<string, string> noEnv = name => null;
            var o = Options.Parse(new string[0], noEnv);
            Equal("2026-11-03", Fmt.Date(o.Today), "Default today is 2026-11-03");
            Equal("v1", o.Release, "Default release is v1");
            o = Options.Parse(new[] { "--release", "V3", "--release-seed", "7", "--drift", "10", "--today", "2027-01-15", "--reset" }, noEnv);
            Check(o.Release == "v3" && o.ReleaseSeed == 7 && o.DriftSeconds == 10 && o.Reset && Fmt.Date(o.Today) == "2027-01-15",
                  "Command-line options are read", "");
            Func<string, string> env = name => name == "POLICYDESK_RELEASE" ? "v2" : name == "POLICYDESK_LATENCY_MS" ? "0" : null;
            o = Options.Parse(new string[0], env);
            Check(o.Release == "v2" && o.LatencyMs == 0, "Environment variables are read", "");
            o = Options.Parse(new[] { "--release", "v4" }, env);
            Equal("v4", o.Release, "The command line wins over the environment");
            foreach (var bad in new[] { new[] { "--release", "v9" }, new[] { "--drift", "1" }, new[] { "--today", "03/11/2026" },
                                        new[] { "--bogus" }, new[] { "--latency-ms" } })
            {
                bool threw = false;
                try { Options.Parse(bad, noEnv); } catch (OptionsException) { threw = true; }
                Check(threw, "Rejected: " + string.Join(" ", bad), "");
            }
        }

        static void CsvChecks(DateTime today)
        {
            var store = Store.Seed(today);
            var odd = Input("Auto", "Standard", 500);
            odd.Policyholder = "Smith, \"Jo\"";
            store.AddQuote(odd, "t", "Boston");
            string csv = MainForm.Csv(store.Quotes);
            var lines = csv.Split(new[] { "\r\n" }, StringSplitOptions.RemoveEmptyEntries);
            Equal(7, lines.Length, "Header plus one line per quote");
            Check(lines[6].StartsWith("1006,\"Smith, \"\"Jo\"\"\",Individual,Auto,"), "Commas and quotes are escaped", lines[6]);
            Check(lines[2].Contains(",1190.25,Monthly,"), "Premium is written without a thousands separator", lines[2]);
        }

        static List<Dictionary<string, object>> Elements(JObj map)
        {
            var text = Json.Write(map);
            var root = (Dictionary<string, object>)Json.Parse(text);
            return Json.Arr(root, "elements").Cast<Dictionary<string, object>>().ToList();
        }

        static void ReleaseChecks(DateTime today)
        {
            var rules = ReleaseRules.Load(ReleaseRules.DefaultPath);
            var options = new Options { Today = today };
            Equal("v1,v2,v3,v4", string.Join(",", rules.Releases.ToArray()), "Four releases");

            var v1Ui = new ReleaseUi(rules, "v1", -1);
            var v1 = Elements(LocatorMap.Build(v1Ui, options));
            var keys = new HashSet<string>(v1.Select(e => (string)e["key"]));
            Check(v1.Count > 80, "The v1 map lists every authored element", v1.Count + " elements");
            Check(v1.Where(e => (string)e["automation_id"] != "").All(e => (string)e["automation_id"] == (string)e["key"]),
                  "v1 AutomationIds are the authored names", "");
            Check(v1.Where(e => (string)e["automation_id"] == "").All(e => (bool)e["legacy"] || e.ContainsKey("note")),
                  "Every element without an AutomationId says how to reach it", "");
            Check(v1.Count(e => (bool)e["legacy"]) == 5, "Five legacy elements: four toolbar buttons and the Notes box",
                  string.Join(",", v1.Where(e => (bool)e["legacy"]).Select(e => (string)e["key"]).ToArray()));
            Equal("Policyholder:", Name(v1, "txtPolicyholder"), "An edit's Name comes from its label");

            foreach (var release in rules.Releases)
            {
                var unknown = new List<string>();
                Dictionary<string, string> map;
                if (rules.Names.TryGetValue(release, out map)) unknown.AddRange(map.Keys.Where(k => !keys.Contains(k)));
                if (rules.Types.TryGetValue(release, out map)) unknown.AddRange(map.Keys.Where(k => !keys.Contains(k)));
                List<string[]> swaps;
                if (rules.Swaps.TryGetValue(release, out swaps)) unknown.AddRange(swaps.SelectMany(p => p).Where(k => !keys.Contains(k)));
                Check(unknown.Count == 0, release + " name, type and swap rules all name real elements", string.Join(",", unknown.ToArray()));
            }
            var unused = rules.Text.SelectMany(r => r.Value.Keys.Select(k => r.Key + ": " + k))
                                   .Where(k => !v1Ui.State.AskedTexts.Contains(k.Substring(4))).ToList();
            Check(unused.Count == 0, "Every text rule matches an authored text", string.Join(" | ", unused.ToArray()));

            var v2 = Elements(LocatorMap.Build(new ReleaseUi(rules, "v2", -1), options));
            var missing = v1Ui.Entries.Where(e => !e.Legacy && ReleaseRules.Lookup(rules.Names, "v2", e.Key) == null)
                                      .Select(e => e.Key).Distinct().ToList();
            Check(missing.Count == 0, "v2 renames every named element", string.Join(",", missing.ToArray()));
            Check(v2.Where(e => (string)e["automation_id"] != "").All(e => (string)e["automation_id"] != (string)e["key"]),
                  "No v2 AutomationId keeps its v1 value", "");
            UniqueIds(v2, "v2");
            Equal("Log in", Name(v2, "btnSignIn"), "v2 button text: Sign in -> Log in");
            Equal("Policyholder:", Name(v2, "txtPolicyholder"), "v2 keeps the labels");
            Equal("mtbPhone", Swapped(v2, "txtEmail"), "v2 swaps Email and Phone");
            Check(!(bool)Root(new ReleaseUi(rules, "v2", -1), options)["dynamic"], "v2 is not dynamic", "");

            var v3a = Elements(LocatorMap.Build(new ReleaseUi(rules, "v3", 42), options));
            var v3b = Elements(LocatorMap.Build(new ReleaseUi(rules, "v3", 42), options));
            var v3c = Elements(LocatorMap.Build(new ReleaseUi(rules, "v3", -1), options));
            UniqueIds(v3a, "v3");
            Check(Ids(v3a) == Ids(v3b), "v3 with --release-seed is repeatable", "");
            Check(Ids(v3a) != Ids(v3c), "v3 without a seed differs", "");
            Check(v3a.Where(e => (string)e["automation_id"] != "").All(e => ((string)e["automation_id"]).StartsWith("c")), "v3 ids are random", "");
            Check((bool)Root(new ReleaseUi(rules, "v3", -1), options)["dynamic"], "v3 without a seed is dynamic", "");
            Check(!(bool)Root(new ReleaseUi(rules, "v3", 42), options)["dynamic"], "v3 with a seed is not dynamic", "");

            var v4 = Elements(LocatorMap.Build(new ReleaseUi(rules, "v4", 1), options));
            UniqueIds(v4, "v4");
            var notLinks = rules.Types["v4"].Keys.Where(k =>
            {
                var e = v4.First(x => (string)x["key"] == k);
                return (string)e["control_type"] != "Text" || !((string)e["note"]).Contains("Hyperlink");
            }).ToList();
            Check(notLinks.Count == 0, "v4 turns " + rules.Types["v4"].Count + " buttons into link labels", string.Join(",", notLinks.ToArray()));
            Equal("Insured name:", Name(v4, "txtPolicyholder"), "v4 rewords labels: Policyholder -> Insured name");
            Equal("txtPassword", Swapped(v4, "txtUser"), "v4 swaps User name and Password");

            var drifting = new ReleaseUi(rules, "v1", 5);
            var live = new LoginForm(drifting, options, Store.Seed(today));
            string before = live.Controls.Cast<System.Windows.Forms.Control>().First(c => c is System.Windows.Forms.TextBox).Name;
            drifting.Drift();
            string after = live.Controls.Cast<System.Windows.Forms.Control>().First(c => c is System.Windows.Forms.TextBox).Name;
            live.Dispose();
            Check(before == "txtUser" && after != before && after.StartsWith("c"), "Drift renames controls already on screen", before + " -> " + after);
            var drifted = Root(drifting, options);
            Check((bool)drifted["dynamic"] && (int)drifted["drift_generation"] == 1, "After drift the map is dynamic, generation 1", "");
        }

        // Each defect that lives in the business rules must change their result; the clean build must not.
        static void DefectChecks(DateTime today)
        {
            Func<string, string> noEnv = name => null;
            Equal(0, Options.Parse(new string[0], noEnv).Defects.Count, "No defects by default");
            Equal(2, Options.Parse(new[] { "--defects", " no_lockout , phantom_bind " }, noEnv).Defects.Count, "--defects reads a list");
            Func<string, string> env = name => name == "POLICYDESK_DEFECTS" ? "slow_underwriting" : null;
            Check(Options.Parse(new string[0], env).Defects.Contains("slow_underwriting"), "POLICYDESK_DEFECTS is read", "");
            foreach (var bad in new[] { new[] { "--defects", "nope" }, new[] { "--defects", "no_lockout,typo" } })
            {
                bool threw = false;
                try { Options.Parse(bad, noEnv); } catch (OptionsException) { threw = true; }
                Check(threw, "Unknown defect refused: " + bad[1], "");
            }
            try
            {
                var renters = Input("Renters", "Basic", 250);
                renters.PaymentPlan = "Monthly";
                Defects.Set(new[] { "monthly_truncated" });
                Equal(16.99m, Pricing.Calculate(renters).Installment, "monthly_truncated: 16.99 instead of 17.00");

                var umbrella = Input("Umbrella", "Standard", 500);
                Defects.Set(new[] { "umbrella_unchecked" });
                Equal("Approved", Pricing.Underwrite(umbrella, Pricing.Calculate(umbrella), Store.Seed(today).Quotes).Decision,
                      "umbrella_unchecked: Umbrella approved without a bound policy");

                var start = Input("Auto", "Standard", 500);
                start.StartDate = today;
                Defects.Set(new[] { "start_today_rejected" });
                Equal(1, Pricing.Validate(start, today).Count, "start_today_rejected: today is refused");

                var store = Store.Seed(today);
                var multi = Input("Auto", "Standard", 500);
                multi.MultiPolicy = true;
                Defects.Set(new[] { "multi_policy_ignored" });
                Equal(540.00m, Pricing.Calculate(multi).Annual, "multi_policy_ignored: the screen still shows 540.00");
                Equal(600.00m, store.AddQuote(multi, "t", "Boston").Price.Annual, "multi_policy_ignored: the saved quote is 600.00");

                Defects.Set(new[] { "phantom_bind" });
                store.Bind(store.Find(1002), "t");
                Equal("Pending", store.Find(1002).Status, "phantom_bind: the quote stays Pending");
            }
            finally
            {
                Defects.Set(new string[0]);
            }
            Equal(17.00m, Pricing.Calculate(new QuoteInput { Product = "Renters", Coverage = "Basic", Deductible = 250 }).Installment,
                  "With the defects off again, 17.00");
        }

        static Dictionary<string, object> Root(ReleaseUi ui, Options options)
        {
            return (Dictionary<string, object>)Json.Parse(Json.Write(LocatorMap.Build(ui, options)));
        }

        static string Name(List<Dictionary<string, object>> map, string key)
        {
            var e = map.FirstOrDefault(x => (string)x["key"] == key);
            return e == null ? null : (string)e["name"];
        }

        static string Swapped(List<Dictionary<string, object>> map, string key)
        {
            var e = map.FirstOrDefault(x => (string)x["key"] == key);
            object v;
            return e != null && e.TryGetValue("swapped_with", out v) ? (string)v : null;
        }

        static string Ids(List<Dictionary<string, object>> map)
        {
            return string.Join(",", map.Select(e => (string)e["automation_id"]).ToArray());
        }

        static void UniqueIds(List<Dictionary<string, object>> map, string release)
        {
            var dupes = map.Where(e => (string)e["automation_id"] != "").GroupBy(e => (string)e["window"] + "/" + (string)e["automation_id"])
                           .Where(g => g.Count() > 1).Select(g => g.Key).ToList();
            Check(dupes.Count == 0, release + " AutomationIds are unique in each window", string.Join(",", dupes.ToArray()));
        }
    }
}
