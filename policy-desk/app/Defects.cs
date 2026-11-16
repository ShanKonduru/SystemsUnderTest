// Planted defects, for test runs only: a "buggy build" switched on with --defects a,b or POLICYDESK_DEFECTS=a,b.
//
// Each defect copies a real class of desktop-app bug, so a test suite (the baseline suite in tests\, or generated
// scripts) can show that it catches it. Normal runs leave them off. An unknown name is an error, so a typo never
// runs a clean build by accident. Where each one lives:
//   Domain.cs  multi_policy_ignored, monthly_truncated, umbrella_unchecked, start_today_rejected, phantom_bind
//   Forms.cs   no_lockout, grid_not_refreshed, export_drops_last, slow_underwriting, email_unlabeled
// The app never says which defects are on: not on screen, not in the activity log, not in the locator map.

using System;
using System.Collections.Generic;
using System.Linq;

namespace PolicyDesk
{
    static class Defects
    {
        public static readonly SortedDictionary<string, string> Known = new SortedDictionary<string, string>
        {
            { "multi_policy_ignored", "the screen applies the 10% multi-policy discount, but the saved quote is priced without it" },
            { "monthly_truncated", "monthly installments are cut to the cent instead of rounded (16.99 instead of 17.00)" },
            { "umbrella_unchecked", "Umbrella is approved without a bound Auto or Home policy for the policyholder" },
            { "start_today_rejected", "a start date of today is refused as \"in the past\" (an off-by-one at the boundary)" },
            { "phantom_bind", "Bind policy reports the new policy number, but the quote is never bound or saved" },
            { "no_lockout", "sign-in never locks after 3 wrong passwords" },
            { "grid_not_refreshed", "a saved or updated quote does not show in the Quotes grid until the app restarts" },
            { "export_drops_last", "Export writes every visible quote but the last, while reporting the full count" },
            { "slow_underwriting", "the underwriting check takes 12 seconds, whatever --latency-ms says" },
            { "email_unlabeled", "the Email box loses its label, so UI Automation gives it no Name" },
        };

        static HashSet<string> active = new HashSet<string>();

        public static bool Has(string name) { return active.Contains(name); }

        public static IEnumerable<string> Active { get { return active.OrderBy(x => x); } }

        public static void Set(IEnumerable<string> names) { active = new HashSet<string>(names); }

        // "a, b" -> {a, b}; an unknown name is an error.
        public static HashSet<string> Parse(string what, string value)
        {
            var names = new HashSet<string>((value ?? "").Split(',').Select(n => n.Trim()).Where(n => n.Length > 0));
            var unknown = names.Where(n => !Known.ContainsKey(n)).OrderBy(n => n).ToList();
            if (unknown.Count > 0)
                throw new OptionsException(what + ": unknown defect(s) " + string.Join(", ", unknown.ToArray())
                                           + ". Known: " + string.Join(", ", Known.Keys.ToArray()) + ".");
            return names;
        }
    }
}
