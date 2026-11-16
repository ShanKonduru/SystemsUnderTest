// Harbor Mutual Policy Desk: business rules and the system of record.
//
// Everything a test may want to check against lives here, away from the screens: the price of a quote, the
// underwriting decision, field validation, and the JSON store (data\policydesk.json) that holds quotes, settings
// and the activity log. "Today" is fixed by the launch options, so every run prices and dates the same way.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace PolicyDesk
{
    static class Fmt
    {
        public static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        public static string Money(decimal value) { return value.ToString("#,##0.00", Inv); }
        public static string Date(DateTime value) { return value.ToString("yyyy-MM-dd", Inv); }

        public static DateTime ParseDate(string text)
        {
            return DateTime.ParseExact(text, "yyyy-MM-dd", Inv, DateTimeStyles.None);
        }

        public static string Phone(string digits)
        {
            if (digits == null || digits.Length != 10) return digits ?? "";
            return "(" + digits.Substring(0, 3) + ") " + digits.Substring(3, 3) + "-" + digits.Substring(6);
        }

        public static string Plan(Quote quote)
        {
            return quote.Input.PaymentPlan == "Monthly" ? "12 x " + Money(quote.Price.Installment) : "Annual";
        }

        public static string StripMnemonic(string text)
        {
            return (text ?? "").Replace("&&", "\u0001").Replace("&", "").Replace("\u0001", "&");
        }
    }

    // ── catalogue and pricing ───────────────────────────────────────────────
    static class Catalog
    {
        public static readonly string[] Products = { "Auto", "Home", "Renters", "Umbrella" };
        public static readonly string[] Coverages = { "Basic", "Standard", "Premium" };
        public static readonly int[] Deductibles = { 250, 500, 1000 };
        public static readonly string[] CustomerTypes = { "Individual", "Business" };
        public static readonly string[] Offices = { "Boston", "Portland", "Providence", "Hartford" };
        public static readonly string[] Statuses = { "Pending", "Referred", "Bound", "Declined" };
        public static readonly string[] DeclineReasons = { "Customer withdrew", "Price too high", "Underwriting decision", "Other" };

        public const decimal BusinessLoading = 0.15m;
        public const decimal MultiPolicyDiscount = 0.10m;
        public const decimal RoadsideFee = 45.00m;
        public const decimal InstallmentFee = 0.03m;
        public const decimal ReferralThreshold = 1500.00m;
        public const int MaxStartDays = 60;
        public const int MaxPolicyholderLength = 60;

        public static decimal BasePremium(string product)
        {
            switch (product)
            {
                case "Auto": return 480.00m;
                case "Home": return 920.00m;
                case "Renters": return 180.00m;
                case "Umbrella": return 310.00m;
            }
            throw new ArgumentException("Unknown product: " + product);
        }

        public static decimal CoverageFactor(string coverage)
        {
            switch (coverage)
            {
                case "Basic": return 1.00m;
                case "Standard": return 1.25m;
                case "Premium": return 1.60m;
            }
            throw new ArgumentException("Unknown coverage: " + coverage);
        }

        public static decimal DeductibleFactor(int deductible)
        {
            switch (deductible)
            {
                case 250: return 1.10m;
                case 500: return 1.00m;
                case 1000: return 0.90m;
            }
            throw new ArgumentException("Unknown deductible: " + deductible);
        }

        public static string Describe(string product)
        {
            switch (product)
            {
                case "Auto": return "Car insurance for private vehicles. Roadside assistance can be added for 45.00 a year.";
                case "Home": return "Buildings and contents cover for owner-occupied homes.";
                case "Renters": return "Contents and liability cover for tenants.";
                case "Umbrella": return "Extra liability cover on top of a bound Auto or Home policy for the same policyholder.";
            }
            return "";
        }
    }

    class QuoteInput
    {
        public string Policyholder = "";
        public string CustomerType = "Individual";
        public string Email = "";
        public string Phone = "";          // digits only, 0-10 of them
        public string Product = "Auto";
        public string Coverage = "Standard";
        public int Deductible = 500;
        public DateTime StartDate;
        public bool Roadside;
        public bool MultiPolicy;
        public string PaymentPlan = "Annual";
        public string Notes = "";

        public QuoteInput Clone() { return (QuoteInput)MemberwiseClone(); }
    }

    class Price
    {
        public decimal Annual;       // the yearly premium
        public decimal Installment;  // one of 12 monthly payments (premium plus the installment fee)
        public decimal PlanTotal;    // what the customer pays in a year on the chosen plan
    }

    class UwResult
    {
        public string Decision;  // Approved or Referred
        public string Reason;
        public UwResult(string decision, string reason) { Decision = decision; Reason = reason; }
    }

    class FieldError
    {
        public string Field;     // policyholder, contact, email, phone, start
        public string Message;
        public FieldError(string field, string message) { Field = field; Message = message; }
    }

    static class Pricing
    {
        public static decimal Round(decimal value) { return Math.Round(value, 2, MidpointRounding.AwayFromZero); }

        // base x coverage, x deductible, + business loading, - multi-policy discount (each step rounded to cents),
        // + roadside fee (Auto only). Monthly: 12 payments of (premium x 1.03 / 12), rounded to cents.
        public static Price Calculate(QuoteInput input)
        {
            decimal premium = Round(Catalog.BasePremium(input.Product) * Catalog.CoverageFactor(input.Coverage));
            premium = Round(premium * Catalog.DeductibleFactor(input.Deductible));
            if (input.CustomerType == "Business") premium = Round(premium * (1 + Catalog.BusinessLoading));
            if (input.MultiPolicy) premium = Round(premium * (1 - Catalog.MultiPolicyDiscount));
            if (input.Roadside && input.Product == "Auto") premium += Catalog.RoadsideFee;
            decimal installment = Defects.Has("monthly_truncated")
                ? Math.Truncate(premium * (1 + Catalog.InstallmentFee) / 12 * 100) / 100
                : Round(premium * (1 + Catalog.InstallmentFee) / 12);
            return new Price
            {
                Annual = premium,
                Installment = installment,
                PlanTotal = input.PaymentPlan == "Monthly" ? installment * 12 : premium,
            };
        }

        public static UwResult Underwrite(QuoteInput input, Price price, IEnumerable<Quote> existing)
        {
            if (input.Product == "Umbrella" && !Defects.Has("umbrella_unchecked"))
            {
                string holder = input.Policyholder.Trim();
                bool covered = existing.Any(q => q.Status == "Bound"
                    && (q.Input.Product == "Auto" || q.Input.Product == "Home")
                    && string.Equals(q.Input.Policyholder.Trim(), holder, StringComparison.OrdinalIgnoreCase));
                if (!covered) return new UwResult("Referred", "Umbrella needs a bound Auto or Home policy for this policyholder.");
            }
            if (price.Annual > Catalog.ReferralThreshold)
                return new UwResult("Referred", "Premium over " + Fmt.Money(Catalog.ReferralThreshold) + " needs an underwriter.");
            return new UwResult("Approved", "Within the desk's authority.");
        }

        static readonly Regex EmailPattern = new Regex(@"^[^@\s]+@[^@\s]+\.[^@\s]+$");

        public static List<FieldError> Validate(QuoteInput input, DateTime today)
        {
            var errors = new List<FieldError>();
            string holder = input.Policyholder.Trim();
            if (holder.Length == 0) errors.Add(new FieldError("policyholder", "Policyholder is required."));
            else if (holder.Length > Catalog.MaxPolicyholderLength)
                errors.Add(new FieldError("policyholder", "Policyholder must be at most " + Catalog.MaxPolicyholderLength + " characters."));
            string email = input.Email.Trim();
            if (email.Length == 0 && input.Phone.Length == 0)
                errors.Add(new FieldError("contact", "Enter an email address or a phone number."));
            if (email.Length > 0 && !EmailPattern.IsMatch(email))
                errors.Add(new FieldError("email", "Enter a valid email address."));
            if (input.Phone.Length > 0 && input.Phone.Length != 10)
                errors.Add(new FieldError("phone", "Enter a 10-digit phone number."));
            if (input.StartDate.Date < today.Date || (Defects.Has("start_today_rejected") && input.StartDate.Date == today.Date))
                errors.Add(new FieldError("start", "Start date cannot be in the past."));
            else if (input.StartDate.Date > today.Date.AddDays(Catalog.MaxStartDays))
                errors.Add(new FieldError("start", "Start date must be within " + Catalog.MaxStartDays + " days."));
            return errors;
        }
    }

    // ── records ─────────────────────────────────────────────────────────────
    class Quote
    {
        public int Number;
        public QuoteInput Input;
        public Price Price;
        public string Status = "Pending";   // Pending, Referred, Bound, Declined
        public string Underwriting = "";
        public string PolicyNumber = "";
        public string DeclineReason = "";
        public string CreatedBy = "";
        public string Office = "";
        public DateTime Created;
        public DateTime Updated;

        public bool Editable { get { return Status == "Pending" || Status == "Referred"; } }
    }

    class ActivityRecord
    {
        public int Seq;
        public DateTime Date;
        public string User = "";
        public string Kind = "";
        public string Detail = "";
    }

    class Settings
    {
        public string DefaultOffice = "Boston";
        public string DefaultPlan = "Annual";
        public bool ConfirmDiscard = true;
        public bool ShowChart = true;
    }

    // ── the system of record: data\policydesk.json ──────────────────────────
    class Store
    {
        public const string FileName = "policydesk.json";
        public string Dir;                 // null: in memory only (locator maps, self-test)
        public DateTime Today;
        public int NextNumber = 1001;
        public List<Quote> Quotes = new List<Quote>();
        public List<ActivityRecord> Activity = new List<ActivityRecord>();
        public Settings Settings = new Settings();
        public event EventHandler Changed;

        public string FilePath { get { return Dir == null ? null : Path.Combine(Dir, FileName); } }

        public static Store Open(string dir, bool reset, DateTime today)
        {
            Directory.CreateDirectory(dir);
            string path = Path.Combine(dir, FileName);
            Store store;
            if (reset || !File.Exists(path))
            {
                store = Seed(today);
                store.Dir = dir;
                store.Save();
            }
            else
            {
                try { store = Load(File.ReadAllText(path, Encoding.UTF8), today); }
                catch (Exception ex)
                {
                    if (ex is IOException) throw;
                    throw new InvalidDataException(path + " cannot be read (" + ex.Message + "). Start with --reset to recreate it.");
                }
                store.Dir = dir;
            }
            return store;
        }

        // Five quotes, one of each status, so every screen and filter has something to show.
        public static Store Seed(DateTime today)
        {
            var store = new Store { Today = today };
            store.SeedQuote("Dana Whitfield", "Individual", "dana.whitfield@example.com", "6175550142", "Auto", "Standard", 500,
                            today.AddDays(-5), true, false, "Annual", "Bound", "", today.AddDays(-20), "jmorgan", "Boston");
            store.SeedQuote("Northwind Traders", "Business", "ops@northwind.example", "", "Home", "Standard", 1000,
                            today.AddDays(14), false, false, "Monthly", "Pending", "", today.AddDays(-6), "jmorgan", "Boston");
            store.SeedQuote("Priya Raman", "Individual", "priya.raman@example.com", "4015550199", "Renters", "Basic", 250,
                            today.AddDays(10), false, false, "Annual", "Pending", "", today.AddDays(-3), "akim", "Providence");
            store.SeedQuote("Marcus Lee", "Individual", "", "8605550123", "Umbrella", "Standard", 500,
                            today.AddDays(21), false, false, "Annual", "Referred", "", today.AddDays(-2), "akim", "Hartford");
            store.SeedQuote("Elena Ruiz", "Individual", "elena.ruiz@example.com", "", "Home", "Premium", 250,
                            today.AddDays(30), false, true, "Monthly", "Declined", "Price too high", today.AddDays(-1), "jmorgan", "Portland");
            store.Log("system", "reset", "Seed data loaded: " + store.Quotes.Count + " quotes");
            return store;
        }

        void SeedQuote(string holder, string type, string email, string phone, string product, string coverage, int deductible,
                       DateTime start, bool roadside, bool multi, string plan, string status, string declineReason,
                       DateTime created, string by, string office)
        {
            var input = new QuoteInput
            {
                Policyholder = holder, CustomerType = type, Email = email, Phone = phone, Product = product, Coverage = coverage,
                Deductible = deductible, StartDate = start, Roadside = roadside, MultiPolicy = multi, PaymentPlan = plan,
            };
            var price = Pricing.Calculate(input);
            var uw = Pricing.Underwrite(input, price, Quotes);
            var quote = new Quote
            {
                Number = NextNumber++, Input = input, Price = price, Status = status, Underwriting = uw.Reason,
                DeclineReason = declineReason, CreatedBy = by, Office = office, Created = created, Updated = created,
            };
            if (status == "Bound") quote.PolicyNumber = "HMP-" + quote.Number;
            Quotes.Add(quote);
        }

        public Quote Find(int number) { return Quotes.FirstOrDefault(q => q.Number == number); }

        // The price the store records. With multi_policy_ignored it disagrees with the screen.
        static Price StoredPrice(QuoteInput input)
        {
            if (!Defects.Has("multi_policy_ignored")) return Pricing.Calculate(input);
            var priced = input.Clone();
            priced.MultiPolicy = false;
            return Pricing.Calculate(priced);
        }

        public Quote AddQuote(QuoteInput input, string user, string office)
        {
            var price = StoredPrice(input);
            var uw = Pricing.Underwrite(input, price, Quotes);
            var quote = new Quote
            {
                Number = NextNumber++, Input = input.Clone(), Price = price, Status = StatusFor(uw), Underwriting = uw.Reason,
                CreatedBy = user, Office = office, Created = Today, Updated = Today,
            };
            Quotes.Add(quote);
            Log(user, "quote_saved", quote.Number + " " + input.Product + " " + Fmt.Money(price.Annual) + " " + quote.Status);
            return quote;
        }

        public void UpdateQuote(Quote quote, QuoteInput input, string user)
        {
            if (!quote.Editable) throw new InvalidOperationException("Quote " + quote.Number + " is " + quote.Status + ".");
            var price = StoredPrice(input);
            var uw = Pricing.Underwrite(input, price, Quotes.Where(q => q != quote));
            quote.Input = input.Clone();
            quote.Price = price;
            quote.Status = StatusFor(uw);
            quote.Underwriting = uw.Reason;
            quote.Updated = Today;
            Log(user, "quote_updated", quote.Number + " " + input.Product + " " + Fmt.Money(price.Annual) + " " + quote.Status);
        }

        public void Bind(Quote quote, string user)
        {
            if (quote.Status != "Pending") throw new InvalidOperationException("Only Pending quotes can be bound.");
            if (Defects.Has("phantom_bind")) return;   // the caller still reports success
            quote.Status = "Bound";
            quote.PolicyNumber = "HMP-" + quote.Number;
            quote.Updated = Today;
            Log(user, "quote_bound", quote.Number + " as " + quote.PolicyNumber);
        }

        public void Decline(Quote quote, string reason, string user)
        {
            if (!quote.Editable) throw new InvalidOperationException("Only Pending or Referred quotes can be declined.");
            quote.Status = "Declined";
            quote.DeclineReason = reason;
            quote.Updated = Today;
            Log(user, "quote_declined", quote.Number + ": " + reason);
        }

        static string StatusFor(UwResult uw) { return uw.Decision == "Approved" ? "Pending" : "Referred"; }

        public ActivityRecord Log(string user, string kind, string detail)
        {
            var record = new ActivityRecord
            {
                Seq = Activity.Count == 0 ? 1 : Activity[Activity.Count - 1].Seq + 1,
                Date = Today, User = user ?? "", Kind = kind, Detail = detail ?? "",
            };
            Activity.Add(record);
            Save();
            return record;
        }

        // Atomic: write a temporary file, then swap it in, so a reader never sees half a file.
        public void Save()
        {
            if (Dir != null)
            {
                string path = FilePath, temp = path + ".tmp";
                File.WriteAllText(temp, Json.Write(ToJson()), new UTF8Encoding(false));
                if (File.Exists(path)) File.Replace(temp, path, null);
                else File.Move(temp, path);
            }
            var handler = Changed;
            if (handler != null) handler(this, EventArgs.Empty);
        }

        public JObj ToJson()
        {
            return new JObj
            {
                { "schema", 1 },
                { "today", Fmt.Date(Today) },
                { "next_quote", NextNumber },
                { "settings", new JObj
                    {
                        { "default_office", Settings.DefaultOffice }, { "default_plan", Settings.DefaultPlan },
                        { "confirm_discard", Settings.ConfirmDiscard }, { "show_chart", Settings.ShowChart },
                    } },
                { "quotes", Quotes.Select(QuoteJson).ToList() },
                { "activity", Activity.Select(a => (object)new JObj
                    {
                        { "seq", a.Seq }, { "date", Fmt.Date(a.Date) }, { "user", a.User }, { "kind", a.Kind }, { "detail", a.Detail },
                    }).ToList() },
            };
        }

        static object QuoteJson(Quote q)
        {
            var i = q.Input;
            return new JObj
            {
                { "number", q.Number }, { "status", q.Status }, { "policyholder", i.Policyholder }, { "customer_type", i.CustomerType },
                { "email", i.Email }, { "phone", i.Phone }, { "product", i.Product }, { "coverage", i.Coverage },
                { "deductible", i.Deductible }, { "start_date", Fmt.Date(i.StartDate) }, { "roadside", i.Roadside },
                { "multi_policy", i.MultiPolicy }, { "payment_plan", i.PaymentPlan }, { "notes", i.Notes },
                { "premium", q.Price.Annual }, { "installment", q.Price.Installment }, { "plan_total", q.Price.PlanTotal },
                { "underwriting", q.Underwriting }, { "policy_number", q.PolicyNumber }, { "decline_reason", q.DeclineReason },
                { "created_by", q.CreatedBy }, { "office", q.Office }, { "created", Fmt.Date(q.Created) }, { "updated", Fmt.Date(q.Updated) },
            };
        }

        public static Store Load(string text, DateTime today)
        {
            var root = (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(text);
            var store = new Store { Today = today, NextNumber = Json.Int(root, "next_quote") };
            var settings = Json.Obj(root, "settings");
            if (settings != null)
            {
                store.Settings.DefaultOffice = Json.Str(settings, "default_office", "Boston");
                store.Settings.DefaultPlan = Json.Str(settings, "default_plan", "Annual");
                store.Settings.ConfirmDiscard = Json.Bool(settings, "confirm_discard", true);
                store.Settings.ShowChart = Json.Bool(settings, "show_chart", true);
            }
            foreach (Dictionary<string, object> q in Json.Arr(root, "quotes"))
            {
                var input = new QuoteInput
                {
                    Policyholder = Json.Str(q, "policyholder", ""), CustomerType = Json.Str(q, "customer_type", "Individual"),
                    Email = Json.Str(q, "email", ""), Phone = Json.Str(q, "phone", ""), Product = Json.Str(q, "product", "Auto"),
                    Coverage = Json.Str(q, "coverage", "Standard"), Deductible = Json.Int(q, "deductible"),
                    StartDate = Fmt.ParseDate(Json.Str(q, "start_date", Fmt.Date(today))), Roadside = Json.Bool(q, "roadside", false),
                    MultiPolicy = Json.Bool(q, "multi_policy", false), PaymentPlan = Json.Str(q, "payment_plan", "Annual"),
                    Notes = Json.Str(q, "notes", ""),
                };
                store.Quotes.Add(new Quote
                {
                    Number = Json.Int(q, "number"), Input = input, Status = Json.Str(q, "status", "Pending"),
                    Price = new Price { Annual = Json.Dec(q, "premium"), Installment = Json.Dec(q, "installment"), PlanTotal = Json.Dec(q, "plan_total") },
                    Underwriting = Json.Str(q, "underwriting", ""), PolicyNumber = Json.Str(q, "policy_number", ""),
                    DeclineReason = Json.Str(q, "decline_reason", ""), CreatedBy = Json.Str(q, "created_by", ""),
                    Office = Json.Str(q, "office", ""), Created = Fmt.ParseDate(Json.Str(q, "created", Fmt.Date(today))),
                    Updated = Fmt.ParseDate(Json.Str(q, "updated", Fmt.Date(today))),
                });
            }
            foreach (Dictionary<string, object> a in Json.Arr(root, "activity"))
            {
                store.Activity.Add(new ActivityRecord
                {
                    Seq = Json.Int(a, "seq"), Date = Fmt.ParseDate(Json.Str(a, "date", Fmt.Date(today))), User = Json.Str(a, "user", ""),
                    Kind = Json.Str(a, "kind", ""), Detail = Json.Str(a, "detail", ""),
                });
            }
            return store;
        }
    }

    // ── a small JSON writer (ordered keys, indented) and reader helpers ─────
    class JObj : List<KeyValuePair<string, object>>
    {
        public void Add(string key, object value) { Add(new KeyValuePair<string, object>(key, value)); }
    }

    static class Json
    {
        public static string Write(object value)
        {
            var sb = new StringBuilder();
            WriteValue(sb, value, 0);
            sb.Append('\n');
            return sb.ToString();
        }

        static void WriteValue(StringBuilder sb, object value, int indent)
        {
            if (value == null) sb.Append("null");
            else if (value is string) WriteString(sb, (string)value);
            else if (value is bool) sb.Append((bool)value ? "true" : "false");
            else if (value is decimal) sb.Append(((decimal)value).ToString("0.00", Fmt.Inv));
            else if (value is int || value is long) sb.Append(Convert.ToString(value, Fmt.Inv));
            else if (value is JObj)
            {
                var obj = (JObj)value;
                if (obj.Count == 0) { sb.Append("{}"); return; }
                sb.Append("{\n");
                for (int i = 0; i < obj.Count; i++)
                {
                    sb.Append(' ', indent + 2);
                    WriteString(sb, obj[i].Key);
                    sb.Append(": ");
                    WriteValue(sb, obj[i].Value, indent + 2);
                    sb.Append(i < obj.Count - 1 ? ",\n" : "\n");
                }
                sb.Append(' ', indent).Append('}');
            }
            else if (value is IEnumerable)
            {
                var items = ((IEnumerable)value).Cast<object>().ToList();
                if (items.Count == 0) { sb.Append("[]"); return; }
                bool simple = items.All(x => x == null || x is string || x is bool || x is int || x is decimal);
                if (simple)
                {
                    sb.Append('[');
                    for (int i = 0; i < items.Count; i++) { if (i > 0) sb.Append(", "); WriteValue(sb, items[i], indent); }
                    sb.Append(']');
                    return;
                }
                sb.Append("[\n");
                for (int i = 0; i < items.Count; i++)
                {
                    sb.Append(' ', indent + 2);
                    WriteValue(sb, items[i], indent + 2);
                    sb.Append(i < items.Count - 1 ? ",\n" : "\n");
                }
                sb.Append(' ', indent).Append(']');
            }
            else throw new ArgumentException("Cannot write " + value.GetType().Name + " as JSON.");
        }

        static void WriteString(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }

        public static object Parse(string text) { return new JavaScriptSerializer().DeserializeObject(text); }

        public static Dictionary<string, object> Obj(Dictionary<string, object> d, string key)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) ? v as Dictionary<string, object> : null;
        }

        public static IEnumerable<object> Arr(Dictionary<string, object> d, string key)
        {
            object v;
            if (d != null && d.TryGetValue(key, out v) && v is object[]) return (object[])v;
            return new object[0];
        }

        public static string Str(Dictionary<string, object> d, string key, string fallback)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && v != null ? Convert.ToString(v, Fmt.Inv) : fallback;
        }

        public static int Int(Dictionary<string, object> d, string key)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && v != null ? Convert.ToInt32(v, Fmt.Inv) : 0;
        }

        public static decimal Dec(Dictionary<string, object> d, string key)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && v != null ? Convert.ToDecimal(v, Fmt.Inv) : 0m;
        }

        public static bool Bool(Dictionary<string, object> d, string key, bool fallback)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && v is bool ? (bool)v : fallback;
        }
    }
}
