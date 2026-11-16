// Harbor Mutual Policy Desk: the screens.
//
// Every control is made through ReleaseUi (see Releases.cs) with its authored v1 name, so UI releases and live
// drift can rename it. Some parts are deliberately "legacy", the way real thick clients often are:
//   - the toolbar buttons show only an icon: no text and no control name, only a tooltip
//   - the Notes box has no control name and no label
//   - the premium chart is drawn by hand, so UI Automation sees an empty pane
// Message boxes are standard Win32 dialogs ("#32770") with stable texts.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;

namespace PolicyDesk
{
    static class Look
    {
        public static readonly Color Navy = Color.FromArgb(0x26, 0x35, 0x7f);
        public static readonly Color Orange = Color.FromArgb(0xf0, 0x8a, 0x1c);
        public static readonly Font Body = new Font("Segoe UI", 9f);
        public static readonly Font Strong = new Font("Segoe UI", 12f, FontStyle.Bold);

        public static void Dialog(Form form, int width, int height)
        {
            form.Font = Body;
            form.FormBorderStyle = FormBorderStyle.FixedDialog;
            form.MaximizeBox = false;
            form.MinimizeBox = false;
            form.ShowInTaskbar = false;
            form.StartPosition = FormStartPosition.CenterParent;
            form.ClientSize = new Size(width, height);
        }

        public static ComboBox List(params object[] items)
        {
            var box = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList };
            box.Items.AddRange(items);
            box.SelectedIndex = 0;
            return box;
        }

        public static object[] Items<T>(IEnumerable<T> values) { return values.Select(v => (object)Convert.ToString(v, Fmt.Inv)).ToArray(); }
    }

    // ── sign-in ─────────────────────────────────────────────────────────────
    class LoginForm : Form
    {
        const string W = "LoginForm";
        public const int MaxAttempts = 3;
        readonly TextBox txtUser = new TextBox();
        readonly TextBox txtPassword = new TextBox();
        readonly ComboBox cboOffice = Look.List(Catalog.Offices);
        readonly Label lblError = new Label();
        readonly Control btnSignIn;
        readonly Options options;
        readonly Store store;
        int failures;

        public string UserName { get { return txtUser.Text.Trim(); } }
        public string Office { get { return (string)cboOffice.SelectedItem; } }

        public LoginForm(ReleaseUi ui, Options options, Store store)
        {
            this.options = options;
            this.store = store;
            Look.Dialog(this, 360, 210);
            ui.Reg(this, W, "LoginForm", "Window", Text = ui.T("Sign in - Harbor Mutual Policy Desk"));

            ui.Field(this, W, "&User name:", txtUser, "txtUser", "Edit", 20, 20, 100, 220, 0);
            txtPassword.PasswordChar = '*';
            ui.Field(this, W, "&Password:", txtPassword, "txtPassword", "Edit", 20, 55, 100, 220, 2);
            cboOffice.SelectedItem = store.Settings.DefaultOffice;
            ui.Field(this, W, "&Office:", cboOffice, "cboOffice", "ComboBox", 20, 90, 100, 220, 4);

            lblError.ForeColor = Color.Firebrick;
            lblError.SetBounds(20, 124, 320, 20);
            ui.Reg(lblError, W, "lblError", "Text", "");
            Controls.Add(lblError);

            btnSignIn = ui.Button(W, "btnSignIn", "Sign in", delegate { TrySignIn(); });
            btnSignIn.SetBounds(170, 165, 80, 28);
            btnSignIn.TabIndex = 6;
            var btnCancel = ui.Button(W, "btnCancel", "Cancel", delegate { DialogResult = DialogResult.Cancel; });
            btnCancel.SetBounds(260, 165, 80, 28);
            btnCancel.TabIndex = 7;
            Controls.Add(btnSignIn);
            Controls.Add(btnCancel);
            AcceptButton = btnSignIn as IButtonControl;
            CancelButton = btnCancel as IButtonControl;
            ui.ApplySwaps();
        }

        void TrySignIn()
        {
            if (failures >= MaxAttempts) return;
            if (UserName.Length == 0) { lblError.Text = "Enter your user name."; txtUser.Focus(); return; }
            if (txtPassword.Text != options.Password)
            {
                failures++;
                store.Log(UserName, "sign_in_failed", "attempt " + failures + " of " + MaxAttempts);
                if (failures >= MaxAttempts)
                {
                    lblError.Text = "Too many attempts. Restart Policy Desk.";
                    btnSignIn.Enabled = false;
                    txtPassword.Enabled = false;
                    return;
                }
                lblError.Text = "Invalid user name or password.";
                txtPassword.SelectAll();
                txtPassword.Focus();
                return;
            }
            DialogResult = DialogResult.OK;
        }
    }

    // ── quote details: bind or decline ──────────────────────────────────────
    class QuoteDetailsForm : Form
    {
        const string W = "QuoteDetailsForm";

        public QuoteDetailsForm(ReleaseUi ui, Store store, Quote quote, string user)
        {
            Look.Dialog(this, 400, 400);
            ui.Reg(this, W, "QuoteDetailsForm", "Window", Text = ui.T("Quote") + " " + quote.Number);
            var i = quote.Input;
            var fields = new[]
            {
                new[] { "txtDetailQuote", "Quote:", quote.Number.ToString(Fmt.Inv) },
                new[] { "txtDetailPolicyholder", "Policyholder:", i.Policyholder },
                new[] { "txtDetailProduct", "Product:", i.Product + ", " + i.Coverage },
                new[] { "txtDetailDeductible", "Deductible:", i.Deductible.ToString(Fmt.Inv) },
                new[] { "txtDetailStart", "Start date:", Fmt.Date(i.StartDate) },
                new[] { "txtDetailPremium", "Premium:", Fmt.Money(quote.Price.Annual) },
                new[] { "txtDetailPlan", "Payment plan:", Fmt.Plan(quote) },
                new[] { "txtDetailStatus", "Status:", quote.Status },
                new[] { "txtDetailPolicy", "Policy number:", quote.PolicyNumber },
                new[] { "txtDetailNote", "Underwriting:", quote.Status == "Declined" ? "Declined: " + quote.DeclineReason : quote.Underwriting },
            };
            for (int n = 0; n < fields.Length; n++)
            {
                var box = new TextBox { ReadOnly = true, Text = fields[n][2] };
                ui.Field(this, W, fields[n][1], box, fields[n][0], "Edit", 16, 16 + n * 32, 110, 260, n * 2);
            }

            var btnBind = ui.Button(W, "btnBind", "&Bind policy", delegate
            {
                store.Bind(quote, user);
                DialogResult = DialogResult.OK;
            });
            btnBind.Enabled = quote.Status == "Pending";
            btnBind.SetBounds(106, 356, 90, 28);
            btnBind.TabIndex = 30;
            var btnDecline = ui.Button(W, "btnDecline", "&Decline...", delegate
            {
                using (var decline = new DeclineForm(ui))
                {
                    if (decline.ShowDialog(this) != DialogResult.OK) return;
                    store.Decline(quote, decline.Reason, user);
                    DialogResult = DialogResult.OK;
                }
            });
            btnDecline.Enabled = quote.Editable;
            btnDecline.SetBounds(206, 356, 90, 28);
            btnDecline.TabIndex = 31;
            var btnClose = ui.Button(W, "btnClose", "Close", delegate { DialogResult = DialogResult.Cancel; });
            btnClose.SetBounds(306, 356, 80, 28);
            btnClose.TabIndex = 32;
            Controls.AddRange(new[] { btnBind, btnDecline, btnClose });
            CancelButton = btnClose as IButtonControl;
            ui.ApplySwaps();
        }
    }

    // ── decline reason (a modal on top of a modal) ──────────────────────────
    class DeclineForm : Form
    {
        const string W = "DeclineForm";
        readonly ComboBox cboReason = Look.List(Catalog.DeclineReasons);
        readonly TextBox txtDeclineNote = new TextBox();
        readonly Label lblDeclineError = new Label();

        public string Reason
        {
            get
            {
                string reason = (string)cboReason.SelectedItem;
                string note = txtDeclineNote.Text.Trim();
                return note.Length == 0 ? reason : reason + ": " + note;
            }
        }

        public DeclineForm(ReleaseUi ui)
        {
            Look.Dialog(this, 380, 170);
            ui.Reg(this, W, "DeclineForm", "Window", Text = ui.T("Decline quote"));
            ui.Field(this, W, "&Reason:", cboReason, "cboReason", "ComboBox", 16, 16, 90, 260, 0);
            ui.Field(this, W, "&Note:", txtDeclineNote, "txtDeclineNote", "Edit", 16, 50, 90, 260, 2);
            lblDeclineError.ForeColor = Color.Firebrick;
            lblDeclineError.SetBounds(16, 84, 350, 20);
            ui.Reg(lblDeclineError, W, "lblDeclineError", "Text", "");
            Controls.Add(lblDeclineError);
            var btnConfirm = ui.Button(W, "btnConfirmDecline", "Decline quote", delegate
            {
                if ((string)cboReason.SelectedItem == "Other" && txtDeclineNote.Text.Trim().Length == 0)
                {
                    lblDeclineError.Text = "Enter a note for Other.";
                    txtDeclineNote.Focus();
                    return;
                }
                DialogResult = DialogResult.OK;
            });
            btnConfirm.SetBounds(176, 124, 100, 28);
            btnConfirm.TabIndex = 10;
            var btnCancel = ui.Button(W, "btnCancelDecline", "Cancel", delegate { DialogResult = DialogResult.Cancel; });
            btnCancel.SetBounds(286, 124, 80, 28);
            btnCancel.TabIndex = 11;
            Controls.AddRange(new[] { btnConfirm, btnCancel });
            CancelButton = btnCancel as IButtonControl;
            ui.ApplySwaps();
        }
    }

    // ── Tools > Options ─────────────────────────────────────────────────────
    class OptionsForm : Form
    {
        const string W = "OptionsForm";

        public OptionsForm(ReleaseUi ui, Store store, string user)
        {
            Look.Dialog(this, 400, 250);
            ui.Reg(this, W, "OptionsForm", "Window", Text = ui.T("Options"));
            var s = store.Settings;
            var cboDefaultOffice = Look.List(Catalog.Offices);
            cboDefaultOffice.SelectedItem = s.DefaultOffice;
            ui.Field(this, W, "Default &office:", cboDefaultOffice, "cboDefaultOffice", "ComboBox", 16, 16, 120, 240, 0);

            var grpPlan = ui.Reg(new GroupBox { Text = ui.T("Default payment plan"), TabIndex = 2 }, W, "grpDefaultPlan", "Group", null);
            grpPlan.SetBounds(16, 50, 368, 56);
            var rbDefAnnual = ui.Reg(new RadioButton { Text = ui.T("Pay annually"), Checked = s.DefaultPlan == "Annual", TabIndex = 0 },
                                     W, "rbDefaultAnnual", "RadioButton", null);
            rbDefAnnual.SetBounds(12, 22, 150, 24);
            var rbDefMonthly = ui.Reg(new RadioButton { Text = ui.T("Pay monthly (+3%)"), Checked = s.DefaultPlan == "Monthly", TabIndex = 1 },
                                      W, "rbDefaultMonthly", "RadioButton", null);
            rbDefMonthly.SetBounds(180, 22, 170, 24);
            grpPlan.Controls.AddRange(new Control[] { rbDefAnnual, rbDefMonthly });

            var chkConfirm = ui.Reg(new CheckBox { Text = ui.T("Ask before discarding unsaved changes"), Checked = s.ConfirmDiscard, TabIndex = 3 },
                                    W, "chkConfirmDiscard", "CheckBox", null);
            chkConfirm.SetBounds(16, 116, 360, 24);
            var chkChart = ui.Reg(new CheckBox { Text = ui.T("Show the premium chart"), Checked = s.ShowChart, TabIndex = 4 },
                                  W, "chkShowChart", "CheckBox", null);
            chkChart.SetBounds(16, 146, 360, 24);

            var btnOk = ui.Button(W, "btnOptionsOk", "OK", delegate
            {
                s.DefaultOffice = (string)cboDefaultOffice.SelectedItem;
                s.DefaultPlan = rbDefMonthly.Checked ? "Monthly" : "Annual";
                s.ConfirmDiscard = chkConfirm.Checked;
                s.ShowChart = chkChart.Checked;
                store.Log(user, "settings_saved", "office " + s.DefaultOffice + ", plan " + s.DefaultPlan
                          + ", confirm " + (s.ConfirmDiscard ? "on" : "off") + ", chart " + (s.ShowChart ? "on" : "off"));
                DialogResult = DialogResult.OK;
            });
            btnOk.SetBounds(206, 208, 80, 28);
            btnOk.TabIndex = 10;
            var btnCancel = ui.Button(W, "btnOptionsCancel", "Cancel", delegate { DialogResult = DialogResult.Cancel; });
            btnCancel.SetBounds(296, 208, 80, 28);
            btnCancel.TabIndex = 11;
            Controls.AddRange(new Control[] { grpPlan, chkConfirm, chkChart, btnOk, btnCancel });
            AcceptButton = btnOk as IButtonControl;
            CancelButton = btnCancel as IButtonControl;
            ui.ApplySwaps();
        }
    }

    // ── Help > Product guide: a tree to expand and select ───────────────────
    class ProductGuideForm : Form
    {
        const string W = "ProductGuideForm";

        public ProductGuideForm(ReleaseUi ui)
        {
            Look.Dialog(this, 520, 320);
            ui.Reg(this, W, "ProductGuideForm", "Window", Text = ui.T("Product guide"));
            var tree = ui.Reg(new TreeView { HideSelection = false, TabIndex = 0 }, W, "tvProducts", "Tree", null);
            tree.SetBounds(12, 12, 220, 256);
            var detail = ui.Reg(new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, TabIndex = 1 },
                                W, "txtGuideDetail", "Edit", null);
            detail.SetBounds(244, 12, 264, 256);
            foreach (string product in Catalog.Products)
            {
                var node = tree.Nodes.Add(product, product);
                node.Tag = Catalog.Describe(product);
                foreach (string coverage in Catalog.Coverages)
                {
                    decimal premium = Pricing.Calculate(new QuoteInput { Product = product, Coverage = coverage }).Annual;
                    var child = node.Nodes.Add(product + "/" + coverage, coverage + " - " + Fmt.Money(premium));
                    child.Tag = product + ", " + coverage + " cover: " + Fmt.Money(premium) + " a year for an individual with a 500 deductible, before discounts and extras.";
                }
            }
            tree.AfterSelect += delegate(object sender, TreeViewEventArgs e) { detail.Text = (string)e.Node.Tag; };
            var btnClose = ui.Button(W, "btnGuideClose", "Close", delegate { DialogResult = DialogResult.Cancel; });
            btnClose.SetBounds(428, 280, 80, 28);
            btnClose.TabIndex = 2;
            Controls.AddRange(new[] { tree, detail, btnClose });
            CancelButton = btnClose as IButtonControl;
        }
    }

    // ── a hand-drawn chart: UI Automation sees an empty pane ────────────────
    class PremiumChart : Panel
    {
        public readonly Dictionary<string, decimal> Totals = new Dictionary<string, decimal>();

        public PremiumChart()
        {
            DoubleBuffered = true;
            BackColor = Color.White;
            BorderStyle = BorderStyle.FixedSingle;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.DrawString("Premium by product", Font, Brushes.Black, 8, 6);
            decimal max = 1;
            foreach (var value in Totals.Values) max = Math.Max(max, value);
            int x = 24, barWidth = 60, bottom = Height - 24;
            using (var bar = new SolidBrush(Look.Navy))
            {
                foreach (string product in Catalog.Products)
                {
                    decimal value;
                    Totals.TryGetValue(product, out value);
                    int height = (int)((bottom - 40) * (value / max));
                    e.Graphics.FillRectangle(bar, x, bottom - height, barWidth, height);
                    e.Graphics.DrawString(product, Font, Brushes.Black, x, bottom + 4);
                    x += barWidth + 36;
                }
            }
        }
    }

    // ── main window ─────────────────────────────────────────────────────────
    class MainForm : Form
    {
        const string W = "MainForm";
        readonly ReleaseUi ui;
        readonly Options options;
        readonly Store store;
        readonly bool live;
        string user = "";
        string office = "";
        bool signedIn;

        readonly TabControl tabs = new TabControl();
        TabPage tabQuote, tabQuotes, tabActivity;

        // quote entry
        readonly TextBox txtPolicyholder = new TextBox();
        readonly ComboBox cboCustomerType = Look.List(Catalog.CustomerTypes);
        readonly TextBox txtEmail = new TextBox();
        readonly MaskedTextBox mtbPhone = new MaskedTextBox("(000) 000-0000");
        readonly ComboBox cboProduct = Look.List(Catalog.Products);
        readonly ComboBox cboCoverage = Look.List(Catalog.Coverages);
        readonly ComboBox cboDeductible = Look.List(Look.Items(Catalog.Deductibles));
        readonly DateTimePicker dtpStart = new DateTimePicker();
        readonly CheckBox chkRoadside = new CheckBox();
        readonly CheckBox chkMultiPolicy = new CheckBox();
        readonly RadioButton rbAnnual = new RadioButton();
        readonly RadioButton rbMonthly = new RadioButton();
        readonly TextBox notes = new TextBox();           // legacy: no control name, no label
        readonly Label lblPremium = new Label();
        readonly Label lblInstallment = new Label();
        readonly Label lblUnderwriting = new Label();
        readonly ProgressBar prgUnderwriting = new ProgressBar();
        readonly Label lblErrors = new Label();
        readonly PremiumChart chart = new PremiumChart();
        readonly ErrorProvider errors = new ErrorProvider();
        Control btnCalculate, btnCheck, btnSave, btnClear;

        // quotes list
        readonly TextBox txtFilter = new TextBox();
        readonly ComboBox cboStatusFilter = Look.List(new object[] { "All", "Pending", "Referred", "Bound", "Declined" });
        readonly DataGridView dgvQuotes = new DataGridView();
        readonly ContextMenuStrip ctxQuotes = new ContextMenuStrip();

        // activity
        readonly ListView lvActivity = new ListView();

        // status bar
        readonly ToolStripStatusLabel lblStatus = new ToolStripStatusLabel();
        readonly ToolStripStatusLabel lblCount = new ToolStripStatusLabel();
        readonly ToolStripStatusLabel lblRelease = new ToolStripStatusLabel();
        readonly ToolStripStatusLabel lblUser = new ToolStripStatusLabel();

        readonly Timer underwritingTimer = new Timer();
        readonly Timer driftTimer = new Timer();
        bool loading;
        bool dirty;
        int editing;            // number of the quote being edited, 0 for a new quote
        int ticket;             // changes whenever the inputs change, so a late underwriting result is dropped
        UwResult checkedResult; // the underwriting result for the inputs as they are now, or null

        public MainForm(ReleaseUi ui, Options options, Store store, bool live)
        {
            this.ui = ui;
            this.options = options;
            this.store = store;
            this.live = live;
            Font = Look.Body;
            ClientSize = new Size(920, 640);
            MinimumSize = new Size(900, 640);
            StartPosition = FormStartPosition.CenterScreen;
            ui.Reg(this, W, "MainForm", "Window", Text = ui.T("Harbor Mutual Policy Desk 3.0"));

            tabs.Dock = DockStyle.Fill;
            ui.Reg(tabs, W, "tabMain", "Tab", null);
            tabs.TabPages.Add(tabQuote = BuildQuotePage());
            tabs.TabPages.Add(tabQuotes = BuildQuotesPage());
            tabs.TabPages.Add(tabActivity = BuildActivityPage());
            Controls.Add(tabs);
            Controls.Add(BuildToolbar());
            Controls.Add(BuildMenu());
            Controls.Add(BuildStatusBar());
            ui.ApplySwaps();
            ui.T("Update quote");   // used later by EditSelected; asked now so the self-test sees every text rule in use

            ClearForm();
            RefreshQuotes();
            RefreshActivity();
            underwritingTimer.Tick += delegate { FinishUnderwriting(); };
            if (live)
            {
                store.Changed += delegate { RefreshActivity(); };
                Shown += delegate { StartDrift(); SignIn(); };
            }
        }

        // ── layout ───────────────────────────────────────────────────────────
        MenuStrip BuildMenu()
        {
            var menu = ui.Reg(new MenuStrip(), W, "menuMain", "MenuBar", null);
            var file = ui.Menu(W, "mnuFile", "&File", null, Keys.None);
            file.DropDownItems.Add(ui.Menu(W, "mnuNewQuote", "&New quote", delegate { NewQuote(); }, Keys.Control | Keys.N));
            file.DropDownItems.Add(ui.Menu(W, "mnuSaveQuote", "&Save quote", delegate { SaveQuote(); }, Keys.Control | Keys.S));
            file.DropDownItems.Add(ui.Menu(W, "mnuExport", "&Export quotes...", delegate { ExportQuotes(); }, Keys.Control | Keys.E));
            file.DropDownItems.Add(new ToolStripSeparator());
            file.DropDownItems.Add(ui.Menu(W, "mnuExit", "E&xit", delegate { Close(); }, Keys.None));
            var quotes = ui.Menu(W, "mnuQuotes", "&Quotes", null, Keys.None);
            quotes.DropDownItems.Add(ui.Menu(W, "mnuFind", "&Find...", delegate { tabs.SelectedTab = tabQuotes; txtFilter.Focus(); }, Keys.Control | Keys.F));
            quotes.DropDownItems.Add(ui.Menu(W, "mnuOpenQuote", "&Open selected", delegate { OpenSelected(); }, Keys.Control | Keys.O));
            quotes.DropDownItems.Add(ui.Menu(W, "mnuEditQuote", "&Edit selected", delegate { EditSelected(); }, Keys.None));
            var tools = ui.Menu(W, "mnuTools", "&Tools", null, Keys.None);
            tools.DropDownItems.Add(ui.Menu(W, "mnuOptions", "&Options...", delegate { ShowOptions(); }, Keys.None));
            var help = ui.Menu(W, "mnuHelp", "&Help", null, Keys.None);
            help.DropDownItems.Add(ui.Menu(W, "mnuGuide", "&Product guide", delegate { ShowGuide(); }, Keys.F1));
            help.DropDownItems.Add(ui.Menu(W, "mnuAbout", "&About Policy Desk", delegate { ShowAbout(); }, Keys.None));
            menu.Items.AddRange(new ToolStripItem[] { file, quotes, tools, help });
            MainMenuStrip = menu;
            return menu;
        }

        ToolStrip BuildToolbar()
        {
            // Legacy: icon-only buttons with no control names; only the tooltips say what they do.
            var bar = ui.Reg(new ToolStrip(), W, "toolbarMain", "ToolBar", null);
            bar.Items.Add(IconButton("tbNewQuote", Color.SeaGreen, "New quote", delegate { NewQuote(); }));
            bar.Items.Add(IconButton("tbSaveQuote", Look.Navy, "Save quote", delegate { SaveQuote(); }));
            bar.Items.Add(IconButton("tbClear", Color.Firebrick, "Clear form", delegate { ClearRequested(); }));
            bar.Items.Add(IconButton("tbExport", Look.Orange, "Export quotes", delegate { ExportQuotes(); }));
            return bar;
        }

        ToolStripButton IconButton(string key, Color color, string tooltip, EventHandler onClick)
        {
            var image = new Bitmap(16, 16);
            using (var g = Graphics.FromImage(image))
            using (var brush = new SolidBrush(color))
            {
                g.Clear(Color.Transparent);
                g.FillEllipse(brush, 1, 1, 14, 14);
            }
            var button = new ToolStripButton(null, image, onClick) { DisplayStyle = ToolStripItemDisplayStyle.Image, ToolTipText = tooltip };
            ui.Legacy(button, W, key, "Button", "Icon only; tooltip \"" + tooltip + "\". Reach it by position or image.");
            return button;
        }

        StatusStrip BuildStatusBar()
        {
            var bar = ui.Reg(new StatusStrip(), W, "statusMain", "StatusBar", null);
            lblStatus.Spring = true;
            lblStatus.TextAlign = ContentAlignment.MiddleLeft;
            lblRelease.Text = "UI " + ui.Release + (options.DriftSeconds > 0 ? ", drift " + options.DriftSeconds + "s" : "");
            lblUser.Text = "Not signed in";
            var keys = new[] { "lblStatus", "lblCount", "lblRelease", "lblUser" };
            var labels = new[] { lblStatus, lblCount, lblRelease, lblUser };
            for (int n = 0; n < labels.Length; n++)
            {
                labels[n].Name = ui.NameOf(keys[n]);
                ui.Entries.Add(new UiEntry { Key = keys[n], Window = W, Target = labels[n], ControlType = "Text", UiaName = "",
                                             Note = "Status bar text: no AutomationId; its Name is its current text, so find it by position in the StatusBar." });
            }
            bar.Items.AddRange(labels);
            return bar;
        }

        const string TabNote = "The tab page: a Pane with this AutomationId, shown only while its tab is selected. "
                               + "The tab to click is a TabItem with the same Name and no AutomationId.";

        TabPage BuildQuotePage()
        {
            var page = ui.Reg(new TabPage(ui.T("New quote")), W, "tabQuote", "Pane", ui.T("New quote"));
            ui.Note(TabNote);
            page.Padding = new Padding(10);

            var holder = ui.Reg(new GroupBox { Text = ui.T("Policyholder"), TabIndex = 0 }, W, "grpPolicyholder", "Group", null);
            holder.SetBounds(12, 10, 430, 160);
            ui.Field(holder, W, "Policy&holder:", txtPolicyholder, "txtPolicyholder", "Edit", 12, 26, 110, 290, 0);
            ui.Field(holder, W, "Customer &type:", cboCustomerType, "cboCustomerType", "ComboBox", 12, 58, 110, 290, 2);
            ui.Field(holder, W, "&Email:", txtEmail, "txtEmail", "Edit", 12, 90, 110, 290, 4);
            mtbPhone.TextMaskFormat = MaskFormat.ExcludePromptAndLiterals;
            mtbPhone.CutCopyMaskFormat = MaskFormat.ExcludePromptAndLiterals;
            ui.Field(holder, W, "P&hone:", mtbPhone, "mtbPhone", "Edit", 12, 122, 110, 290, 6);

            var cover = ui.Reg(new GroupBox { Text = ui.T("Coverage"), TabIndex = 1 }, W, "grpCoverage", "Group", null);
            cover.SetBounds(12, 176, 430, 270);
            ui.Field(cover, W, "&Product:", cboProduct, "cboProduct", "ComboBox", 12, 26, 110, 290, 0);
            ui.Field(cover, W, "Co&verage:", cboCoverage, "cboCoverage", "ComboBox", 12, 58, 110, 290, 2);
            cboDeductible.SelectedItem = "500";
            ui.Field(cover, W, "&Deductible:", cboDeductible, "cboDeductible", "ComboBox", 12, 90, 110, 290, 4);
            dtpStart.Format = DateTimePickerFormat.Custom;
            dtpStart.CustomFormat = "yyyy-MM-dd";
            ui.Field(cover, W, "&Start date:", dtpStart, "dtpStart", "Pane", 12, 122, 110, 290, 6);
            ui.Entries[ui.Entries.Count - 1].UiaName = "";
            ui.Note("A date picker is a Pane whose Name is the date it shows (yyyy-MM-dd), not its label. Type the date, or use the keyboard.");
            chkRoadside.Text = ui.T("&Roadside assistance (+45.00)");
            chkRoadside.SetBounds(122, 156, 290, 24);
            chkRoadside.TabIndex = 8;
            ui.Row(ui.Reg(chkRoadside, W, "chkRoadside", "CheckBox", Fmt.StripMnemonic(chkRoadside.Text)), "chkRoadside");
            chkMultiPolicy.Text = ui.T("&Multi-policy discount (-10%)");
            chkMultiPolicy.SetBounds(122, 184, 290, 24);
            chkMultiPolicy.TabIndex = 9;
            ui.Row(ui.Reg(chkMultiPolicy, W, "chkMultiPolicy", "CheckBox", Fmt.StripMnemonic(chkMultiPolicy.Text)), "chkMultiPolicy");
            rbAnnual.Text = ui.T("Pay &annually");
            rbAnnual.SetBounds(122, 216, 130, 24);
            rbAnnual.TabIndex = 10;
            ui.Row(ui.Reg(rbAnnual, W, "rbAnnual", "RadioButton", Fmt.StripMnemonic(rbAnnual.Text)), "rbAnnual");
            rbMonthly.Text = ui.T("Pay m&onthly (+3%)");
            rbMonthly.SetBounds(262, 216, 150, 24);
            rbMonthly.TabIndex = 11;
            ui.Row(ui.Reg(rbMonthly, W, "rbMonthly", "RadioButton", Fmt.StripMnemonic(rbMonthly.Text)), "rbMonthly");
            cover.Controls.AddRange(new Control[] { chkRoadside, chkMultiPolicy, rbAnnual, rbMonthly });

            notes.Multiline = true;                          // legacy: no Name, no label
            notes.ScrollBars = ScrollBars.Vertical;
            notes.SetBounds(456, 10, 440, 70);
            notes.TabIndex = 50;
            ui.Legacy(notes, W, "notes", "Edit", "Notes box: no control name and no label. Reach it by position.");

            lblPremium.Font = Look.Strong;
            lblPremium.ForeColor = Look.Navy;
            lblPremium.SetBounds(456, 88, 440, 26);
            ui.Reg(lblPremium, W, "lblPremium", "Text", null);
            lblInstallment.SetBounds(456, 116, 440, 20);
            ui.Reg(lblInstallment, W, "lblInstallment", "Text", null);
            lblUnderwriting.SetBounds(456, 140, 440, 20);
            ui.Reg(lblUnderwriting, W, "lblUnderwriting", "Text", null);
            prgUnderwriting.Style = ProgressBarStyle.Marquee;
            prgUnderwriting.MarqueeAnimationSpeed = 30;
            prgUnderwriting.Visible = false;
            prgUnderwriting.SetBounds(456, 162, 440, 12);
            ui.Reg(prgUnderwriting, W, "prgUnderwriting", "ProgressBar", null);
            lblErrors.ForeColor = Color.Firebrick;
            lblErrors.SetBounds(456, 180, 440, 70);
            ui.Reg(lblErrors, W, "lblErrors", "Text", null);

            chart.SetBounds(456, 256, 440, 190);
            ui.Reg(chart, W, "pnlChart", "Pane", null);
            ui.Entries[ui.Entries.Count - 1].Note = "Hand-drawn: UI Automation sees an empty pane. Verify it by image.";

            btnCalculate = ui.Button(W, "btnCalculate", "&Calculate", delegate { Calculate(); });
            btnCalculate.SetBounds(12, 456, 100, 30);
            btnCheck = ui.Button(W, "btnCheck", "Check &underwriting", delegate { StartUnderwriting(); });
            btnCheck.SetBounds(120, 456, 140, 30);
            btnSave = ui.Button(W, "btnSave", "Save quote", delegate { SaveQuote(); });
            btnSave.SetBounds(268, 456, 100, 30);
            btnClear = ui.Button(W, "btnClear", "Clear", delegate { ClearRequested(); });
            btnClear.SetBounds(376, 456, 66, 30);
            int tab = 20;
            foreach (var b in new[] { btnCalculate, btnCheck, btnSave, btnClear }) b.TabIndex = tab++;

            page.Controls.AddRange(new Control[] { holder, cover, notes, lblPremium, lblInstallment, lblUnderwriting,
                                                   prgUnderwriting, lblErrors, chart, btnCalculate, btnCheck, btnSave, btnClear });

            EventHandler changed = delegate { InputChanged(); };
            foreach (var c in new Control[] { txtPolicyholder, txtEmail, mtbPhone }) c.TextChanged += changed;
            foreach (var c in new[] { cboCustomerType, cboProduct, cboCoverage, cboDeductible }) c.SelectedIndexChanged += changed;
            dtpStart.ValueChanged += changed;
            chkRoadside.CheckedChanged += changed;
            chkMultiPolicy.CheckedChanged += changed;
            rbMonthly.CheckedChanged += changed;
            notes.TextChanged += delegate { if (!loading) dirty = true; };
            return page;
        }

        TabPage BuildQuotesPage()
        {
            var page = ui.Reg(new TabPage(ui.T("Quotes")), W, "tabQuotes", "Pane", ui.T("Quotes"));
            ui.Note(TabNote);
            page.Padding = new Padding(10);
            ui.Field(page, W, "Find &policyholder:", txtFilter, "txtFilter", "Edit", 12, 12, 120, 200, 0);
            txtFilter.KeyDown += delegate(object sender, KeyEventArgs e)
            {
                if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; ApplyFilter(); }
            };
            ui.Field(page, W, "S&tatus:", cboStatusFilter, "cboStatusFilter", "ComboBox", 344, 12, 56, 110, 2);
            var btnFind = ui.Button(W, "btnFind", "Find", delegate { ApplyFilter(); });
            btnFind.SetBounds(520, 11, 70, 26);
            var btnOpen = ui.Button(W, "btnOpenQuote", "Open quote", delegate { OpenSelected(); });
            btnOpen.SetBounds(598, 11, 92, 26);
            var btnEdit = ui.Button(W, "btnEditQuote", "Edit quote", delegate { EditSelected(); });
            btnEdit.SetBounds(698, 11, 92, 26);
            var btnExport = ui.Button(W, "btnExport", "Export...", delegate { ExportQuotes(); });
            btnExport.SetBounds(798, 11, 92, 26);
            int tab = 4;
            foreach (var b in new[] { btnFind, btnOpen, btnEdit, btnExport }) b.TabIndex = tab++;

            dgvQuotes.SetBounds(12, 48, 878, 440);
            dgvQuotes.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Bottom;
            dgvQuotes.AllowUserToAddRows = false;
            dgvQuotes.AllowUserToDeleteRows = false;
            dgvQuotes.AllowUserToResizeRows = false;
            dgvQuotes.ReadOnly = true;
            dgvQuotes.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            dgvQuotes.MultiSelect = false;
            dgvQuotes.RowHeadersVisible = false;
            dgvQuotes.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;
            dgvQuotes.TabIndex = 10;
            string[,] columns =
            {
                { "colQuote", "Quote" }, { "colPolicyholder", "Policyholder" }, { "colProduct", "Product" }, { "colCoverage", "Coverage" },
                { "colPremium", "Premium" }, { "colPlan", "Plan" }, { "colStart", "Start" }, { "colStatus", "Status" },
            };
            for (int i = 0; i < columns.GetLength(0); i++) dgvQuotes.Columns.Add(columns[i, 0], ui.T(columns[i, 1]));
            dgvQuotes.Columns["colQuote"].ValueType = typeof(int);
            dgvQuotes.Columns["colPremium"].ValueType = typeof(decimal);
            dgvQuotes.Columns["colPremium"].DefaultCellStyle.Format = "#,##0.00";
            dgvQuotes.Columns["colPremium"].DefaultCellStyle.FormatProvider = Fmt.Inv;
            dgvQuotes.Columns["colPremium"].DefaultCellStyle.Alignment = DataGridViewContentAlignment.MiddleRight;
            dgvQuotes.CellDoubleClick += delegate(object sender, DataGridViewCellEventArgs e) { if (e.RowIndex >= 0) OpenSelected(); };
            dgvQuotes.CellMouseDown += delegate(object sender, DataGridViewCellMouseEventArgs e)
            {
                if (e.Button == MouseButtons.Right && e.RowIndex >= 0) dgvQuotes.CurrentCell = dgvQuotes.Rows[e.RowIndex].Cells[0];
            };
            ui.Reg(dgvQuotes, W, "dgvQuotes", "Table", null);

            ui.Reg(ctxQuotes, W, "ctxQuotes", "Menu", null);
            ctxQuotes.Items.Add(ui.Menu(W, "ctxOpenQuote", "&Open", delegate { OpenSelected(); }, Keys.None));
            ctxQuotes.Items.Add(ui.Menu(W, "ctxEditQuote", "&Edit", delegate { EditSelected(); }, Keys.None));
            ctxQuotes.Items.Add(ui.Menu(W, "ctxCopyNumber", "&Copy quote number", delegate { CopyNumber(); }, Keys.None));
            dgvQuotes.ContextMenuStrip = ctxQuotes;

            page.Controls.AddRange(new Control[] { btnFind, btnOpen, btnEdit, btnExport, dgvQuotes });
            return page;
        }

        TabPage BuildActivityPage()
        {
            var page = ui.Reg(new TabPage(ui.T("Activity")), W, "tabActivity", "Pane", ui.T("Activity"));
            ui.Note(TabNote);
            page.Padding = new Padding(10);
            lvActivity.View = View.Details;
            lvActivity.FullRowSelect = true;
            lvActivity.HideSelection = false;
            lvActivity.SetBounds(12, 12, 878, 440);
            lvActivity.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Bottom;
            string[] headers = { "#", "Date", "User", "Event", "Details" };
            int[] widths = { 50, 90, 100, 150, 470 };
            for (int n = 0; n < headers.Length; n++) lvActivity.Columns.Add(ui.T(headers[n]), widths[n]);
            ui.Reg(lvActivity, W, "lvActivity", "List", null);
            var btnRefresh = ui.Button(W, "btnRefreshActivity", "Refresh", delegate { RefreshActivity(); });
            btnRefresh.SetBounds(810, 460, 80, 28);
            btnRefresh.Anchor = AnchorStyles.Right | AnchorStyles.Bottom;
            page.Controls.AddRange(new Control[] { lvActivity, btnRefresh });
            return page;
        }

        // ── sign-in and lifecycle ────────────────────────────────────────────
        void SignIn()
        {
            using (var login = new LoginForm(ui, options, store))
            {
                if (login.ShowDialog(this) != DialogResult.OK) { Close(); return; }
                user = login.UserName;
                office = login.Office;
            }
            signedIn = true;
            lblUser.Text = "User: " + user + " (" + office + ")";
            store.Log(user, "sign_in", "office " + office + ", release " + ui.Release);
            SetStatus("Signed in");
            txtPolicyholder.Focus();
        }

        // Live drift renames every control, the sign-in dialog included, every N seconds from launch.
        void StartDrift()
        {
            if (options.DriftSeconds <= 0) return;
            driftTimer.Interval = options.DriftSeconds * 1000;
            driftTimer.Tick += delegate
            {
                ui.Drift();
                LocatorMap.WriteLive(ui, options, store.Dir);
                store.Log("system", "drift", "generation " + ui.State.Generation);
            };
            driftTimer.Start();
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (signedIn && !ConfirmDiscard()) { e.Cancel = true; return; }
            if (signedIn && e.CloseReason == CloseReason.UserClosing) store.Log(user, "sign_out", "");
            underwritingTimer.Stop();
            driftTimer.Stop();
            base.OnFormClosing(e);
        }

        bool ConfirmDiscard()
        {
            if (!dirty || !store.Settings.ConfirmDiscard) return true;
            return MessageBox.Show(this, "Discard unsaved changes?", "Policy Desk", MessageBoxButtons.YesNo,
                                   MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2) == DialogResult.Yes;
        }

        // ── quote entry ──────────────────────────────────────────────────────
        QuoteInput ReadInput()
        {
            return new QuoteInput
            {
                Policyholder = txtPolicyholder.Text.Trim(),
                CustomerType = (string)cboCustomerType.SelectedItem,
                Email = txtEmail.Text.Trim(),
                Phone = mtbPhone.Text,
                Product = (string)cboProduct.SelectedItem,
                Coverage = (string)cboCoverage.SelectedItem,
                Deductible = int.Parse((string)cboDeductible.SelectedItem, Fmt.Inv),
                StartDate = dtpStart.Value.Date,
                Roadside = chkRoadside.Checked,
                MultiPolicy = chkMultiPolicy.Checked,
                PaymentPlan = rbMonthly.Checked ? "Monthly" : "Annual",
                Notes = notes.Text,
            };
        }

        void WriteInput(QuoteInput input)
        {
            loading = true;
            txtPolicyholder.Text = input.Policyholder;
            cboCustomerType.SelectedItem = input.CustomerType;
            txtEmail.Text = input.Email;
            mtbPhone.Text = input.Phone;
            cboProduct.SelectedItem = input.Product;
            cboCoverage.SelectedItem = input.Coverage;
            cboDeductible.SelectedItem = input.Deductible.ToString(Fmt.Inv);
            dtpStart.Value = input.StartDate;
            chkRoadside.Checked = input.Roadside;
            chkMultiPolicy.Checked = input.MultiPolicy;
            rbAnnual.Checked = input.PaymentPlan != "Monthly";
            rbMonthly.Checked = input.PaymentPlan == "Monthly";
            notes.Text = input.Notes;
            loading = false;
            ResetUnderwriting();
            UpdateRoadside();
        }

        void InputChanged()
        {
            UpdateRoadside();
            if (loading) return;
            dirty = true;
            ResetUnderwriting();
            lblPremium.Text = "Premium: not calculated";   // the shown price no longer matches the inputs
            lblInstallment.Text = "";
        }

        void UpdateRoadside()
        {
            bool auto = (string)cboProduct.SelectedItem == "Auto";
            if (!auto && chkRoadside.Checked) chkRoadside.Checked = false;
            chkRoadside.Enabled = auto;
        }

        void ResetUnderwriting()
        {
            ticket++;
            underwritingTimer.Stop();
            checkedResult = null;
            prgUnderwriting.Visible = false;
            btnCheck.Enabled = true;
            btnSave.Enabled = false;
            lblUnderwriting.ForeColor = SystemColors.ControlText;
            lblUnderwriting.Text = "Underwriting: not checked";
        }

        void Calculate()
        {
            var input = ReadInput();
            var price = Pricing.Calculate(input);
            lblPremium.Text = "Premium: " + Fmt.Money(price.Annual) + " a year";
            lblInstallment.Text = input.PaymentPlan == "Monthly"
                ? "Monthly: 12 x " + Fmt.Money(price.Installment) + " (" + Fmt.Money(price.PlanTotal) + " a year)"
                : "Paid annually";
        }

        bool ShowErrors(List<FieldError> found)
        {
            errors.Clear();
            var at = new Dictionary<string, Control>
            {
                { "policyholder", txtPolicyholder }, { "contact", txtEmail }, { "email", txtEmail }, { "phone", mtbPhone }, { "start", dtpStart },
            };
            foreach (var error in found) errors.SetError(at[error.Field], error.Message);
            lblErrors.Text = string.Join(Environment.NewLine, found.Select(x => x.Message).ToArray());
            if (found.Count > 0) SetStatus("Fix " + found.Count + " field(s)");
            return found.Count == 0;
        }

        void StartUnderwriting()
        {
            var input = ReadInput();
            if (!ShowErrors(Pricing.Validate(input, options.Today))) return;
            Calculate();
            int mine = ++ticket;
            btnCheck.Enabled = false;
            prgUnderwriting.Visible = true;
            lblUnderwriting.ForeColor = SystemColors.ControlText;
            lblUnderwriting.Text = "Underwriting: checking...";
            SetStatus("Checking underwriting...");
            underwritingTimer.Tag = mine;
            if (options.LatencyMs <= 0) { FinishUnderwriting(); return; }
            underwritingTimer.Interval = options.LatencyMs;
            underwritingTimer.Start();
        }

        void FinishUnderwriting()
        {
            underwritingTimer.Stop();
            if (!(underwritingTimer.Tag is int) || (int)underwritingTimer.Tag != ticket) return;
            var input = ReadInput();
            var price = Pricing.Calculate(input);
            var others = editing == 0 ? store.Quotes : store.Quotes.Where(q => q.Number != editing).ToList();
            checkedResult = Pricing.Underwrite(input, price, others);
            prgUnderwriting.Visible = false;
            btnCheck.Enabled = true;
            btnSave.Enabled = true;
            bool approved = checkedResult.Decision == "Approved";
            lblUnderwriting.ForeColor = approved ? Color.SeaGreen : Look.Orange;
            lblUnderwriting.Text = "Underwriting: " + checkedResult.Decision + (approved ? "" : " - " + checkedResult.Reason);
            store.Log(user, "underwriting_checked", input.Product + " " + Fmt.Money(price.Annual) + ": " + checkedResult.Decision);
            SetStatus("Underwriting " + checkedResult.Decision.ToLowerInvariant());
        }

        void SaveQuote()
        {
            tabs.SelectedTab = tabQuote;
            var input = ReadInput();
            var found = Pricing.Validate(input, options.Today);
            if (found.Any(x => x.Field == "policyholder" && input.Policyholder.Length == 0))
            {
                ShowErrors(found);
                MessageBox.Show(this, "Policyholder is required.", "Policy Desk", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                txtPolicyholder.Focus();
                return;
            }
            if (!ShowErrors(found)) return;
            if (checkedResult == null)
            {
                MessageBox.Show(this, "Check underwriting before saving.", "Policy Desk", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            Quote quote;
            if (editing != 0)
            {
                quote = store.Find(editing);
                store.UpdateQuote(quote, input, user);
            }
            else quote = store.AddQuote(input, user, office);
            string verb = editing != 0 ? "updated" : "saved";
            dirty = false;
            ClearForm();
            RefreshQuotes();
            SetStatus("Quote " + quote.Number + " " + verb + " (" + quote.Status + ")");
        }

        void ClearRequested()
        {
            if (!ConfirmDiscard()) return;
            ClearForm();
            SetStatus("Form cleared");
        }

        void NewQuote()
        {
            if (!ConfirmDiscard()) return;
            ClearForm();
            tabs.SelectedTab = tabQuote;
            txtPolicyholder.Focus();
            SetStatus("New quote");
        }

        void ClearForm()
        {
            editing = 0;
            WriteInput(new QuoteInput { StartDate = options.Today.AddDays(7), PaymentPlan = store.Settings.DefaultPlan });
            dirty = false;
            errors.Clear();
            lblErrors.Text = "";
            tabQuote.Text = ui.T("New quote");
            btnSave.Text = Caption(btnSave, "Save quote");
            Calculate();
        }

        string Caption(Control control, string authored)
        {
            string text = ui.T(authored);
            return control is LinkLabel ? Fmt.StripMnemonic(text) : text;
        }

        // ── quotes list ──────────────────────────────────────────────────────
        void RefreshQuotes()
        {
            int keep = SelectedNumber();
            dgvQuotes.Rows.Clear();
            foreach (var q in store.Quotes)
                dgvQuotes.Rows.Add(q.Number, q.Input.Policyholder, q.Input.Product, q.Input.Coverage, q.Price.Annual,
                                   Fmt.Plan(q), Fmt.Date(q.Input.StartDate), q.Status);
            ApplyFilter(false);
            foreach (DataGridViewRow row in dgvQuotes.Rows)
                if (row.Visible && (int)row.Cells["colQuote"].Value == keep) dgvQuotes.CurrentCell = row.Cells[0];
            lblCount.Text = store.Quotes.Count + " quotes";
            var totals = chart.Totals;
            totals.Clear();
            foreach (var q in store.Quotes.Where(q => q.Status != "Declined"))
            {
                decimal sum;
                totals.TryGetValue(q.Input.Product, out sum);
                totals[q.Input.Product] = sum + q.Price.Annual;
            }
            chart.Visible = store.Settings.ShowChart;
            chart.Invalidate();
        }

        void ApplyFilter() { ApplyFilter(true); }

        void ApplyFilter(bool report)
        {
            string filter = txtFilter.Text.Trim();
            string status = (string)cboStatusFilter.SelectedItem;
            int shown = 0;
            dgvQuotes.CurrentCell = null;
            foreach (DataGridViewRow row in dgvQuotes.Rows)
            {
                bool match = (filter.Length == 0
                              || Convert.ToString(row.Cells["colPolicyholder"].Value).IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0)
                             && (status == "All" || Convert.ToString(row.Cells["colStatus"].Value) == status);
                row.Visible = match;
                if (match) shown++;
            }
            if (!report) return;
            var first = dgvQuotes.Rows.Cast<DataGridViewRow>().FirstOrDefault(r => r.Visible);
            if (first != null) dgvQuotes.CurrentCell = first.Cells[0];   // Open and Edit work on the first match
            SetStatus(shown + " quote(s) match");
        }

        int SelectedNumber()
        {
            var row = dgvQuotes.CurrentRow;
            return row != null && row.Visible ? (int)row.Cells["colQuote"].Value : 0;
        }

        Quote SelectedQuote()
        {
            var quote = store.Find(SelectedNumber());
            if (quote == null)
            {
                tabs.SelectedTab = tabQuotes;
                MessageBox.Show(this, "Select a quote first.", "Policy Desk", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            return quote;
        }

        void OpenSelected()
        {
            var quote = SelectedQuote();
            if (quote == null) return;
            using (var details = new QuoteDetailsForm(ui, store, quote, user))
            {
                if (details.ShowDialog(this) != DialogResult.OK) return;
            }
            RefreshQuotes();
            SetStatus(quote.Status == "Bound" ? "Quote " + quote.Number + " bound as policy " + quote.PolicyNumber
                                              : "Quote " + quote.Number + " declined");
        }

        void EditSelected()
        {
            var quote = SelectedQuote();
            if (quote == null) return;
            if (!quote.Editable)
            {
                MessageBox.Show(this, "Only Pending or Referred quotes can be edited.", "Policy Desk", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            if (!ConfirmDiscard()) return;
            WriteInput(quote.Input);
            editing = quote.Number;
            dirty = false;
            errors.Clear();
            lblErrors.Text = "";
            tabQuote.Text = ui.T("Edit quote") + " " + quote.Number;
            btnSave.Text = Caption(btnSave, "Update quote");
            Calculate();
            tabs.SelectedTab = tabQuote;
            SetStatus("Editing quote " + quote.Number);
        }

        void CopyNumber()
        {
            var quote = SelectedQuote();
            if (quote == null) return;
            Clipboard.SetText(quote.Number.ToString(Fmt.Inv));
            SetStatus("Copied " + quote.Number);
        }

        void ExportQuotes()
        {
            var rows = dgvQuotes.Rows.Cast<DataGridViewRow>().Where(r => r.Visible)
                                .Select(r => store.Find((int)r.Cells["colQuote"].Value)).ToList();
            using (var dialog = new SaveFileDialog
            {
                Title = "Export quotes",
                Filter = "CSV files (*.csv)|*.csv",
                FileName = "quotes-" + Fmt.Date(options.Today) + ".csv",
                InitialDirectory = options.ExportDir ?? Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                OverwritePrompt = true,
            })
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                File.WriteAllText(dialog.FileName, Csv(rows), new UTF8Encoding(false));
                store.Log(user, "quotes_exported", rows.Count + " to " + Path.GetFileName(dialog.FileName));
                SetStatus("Exported " + rows.Count + " quote(s) to " + Path.GetFileName(dialog.FileName));
            }
        }

        public static string Csv(IEnumerable<Quote> quotes)
        {
            var sb = new StringBuilder("Quote,Policyholder,Customer type,Product,Coverage,Deductible,Start date,Premium,Payment plan,Plan total,Status\r\n");
            foreach (var q in quotes)
            {
                var i = q.Input;
                var cells = new[]
                {
                    q.Number.ToString(Fmt.Inv), i.Policyholder, i.CustomerType, i.Product, i.Coverage, i.Deductible.ToString(Fmt.Inv),
                    Fmt.Date(i.StartDate), q.Price.Annual.ToString("0.00", Fmt.Inv), i.PaymentPlan,
                    q.Price.PlanTotal.ToString("0.00", Fmt.Inv), q.Status,
                };
                sb.Append(string.Join(",", cells.Select(CsvCell).ToArray())).Append("\r\n");
            }
            return sb.ToString();
        }

        static string CsvCell(string value)
        {
            if (value.IndexOfAny(new[] { ',', '"', '\r', '\n' }) < 0) return value;
            return "\"" + value.Replace("\"", "\"\"") + "\"";
        }

        // ── other windows ────────────────────────────────────────────────────
        void RefreshActivity()
        {
            if (lvActivity.IsDisposed) return;
            lvActivity.BeginUpdate();
            lvActivity.Items.Clear();
            for (int n = store.Activity.Count - 1; n >= 0; n--)
            {
                var a = store.Activity[n];
                lvActivity.Items.Add(new ListViewItem(new[] { a.Seq.ToString(Fmt.Inv), Fmt.Date(a.Date), a.User, a.Kind, a.Detail }));
            }
            lvActivity.EndUpdate();
        }

        void ShowOptions()
        {
            using (var dialog = new OptionsForm(ui, store, user))
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
            }
            chart.Visible = store.Settings.ShowChart;
            SetStatus("Options saved");
        }

        void ShowGuide()
        {
            using (var dialog = new ProductGuideForm(ui)) dialog.ShowDialog(this);
        }

        void ShowAbout()
        {
            MessageBox.Show(this, ui.T("Harbor Mutual Policy Desk 3.0") + "\nUI release " + ui.Release + ", today " + Fmt.Date(options.Today)
                            + "\nA sample thick client for desktop test automation.", "About Policy Desk",
                            MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        void SetStatus(string text) { lblStatus.Text = text; }
    }
}
