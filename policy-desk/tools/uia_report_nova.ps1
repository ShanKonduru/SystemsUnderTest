<#
.SYNOPSIS
  UI Automation readiness report, NovaWindows view: what Appium's NovaWindows driver can see in a Windows
  desktop app. NovaWindows drives the managed System.Windows.Automation client inside powershell.exe;
  this script uses exactly that client, so its output is what NovaWindows locators can match.
  For the native UIA3 view (Inspect.exe, WinAppDriver / Appium "Windows" driver, FlaUI) run UiaReport.exe;
  uia_report.bat runs both.

.DESCRIPTION
  Starts the app (-Exe) or attaches to a running one (-ProcessName / -WindowTitle), walks the UI Automation
  tree of its top-level window (modal dialogs owned by it are included), and prints every element with its
  ControlType, AutomationId, Name and ClassName. Interactive controls are graded:

    good      has an AutomationId (stable, best locator)
    fair      no AutomationId, but a Name (locator breaks when the caption or language changes)
    poor      neither: only position-based XPath or image recognition can reach it

  A custom-drawn area shows up as a pane with no children: its content is invisible to UI Automation.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\uia_report_nova.ps1 -Exe app\bin\PolicyDesk.exe
  powershell -ExecutionPolicy Bypass -File tools\uia_report_nova.ps1 -ProcessName PolicyDesk -Csv reports\nova.csv

.NOTES
  Read-only: it never clicks or types. With -Exe the app is closed at the end unless -KeepOpen is given.
#>
param(
    [string]$Exe,
    [string]$Arguments = "",
    [string]$ProcessName,
    [string]$WindowTitle,
    [int]$WaitSeconds = 20,
    [int]$MaxDepth = 25,
    [string]$Csv,
    [string]$Json,
    [switch]$KeepOpen,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]

$Interactive = @("Button", "Edit", "ComboBox", "CheckBox", "RadioButton", "ListItem", "MenuItem", "TabItem",
                 "DataItem", "Spinner", "Slider", "Hyperlink", "TreeItem", "SplitButton", "Document", "Table",
                 "DataGrid", "List", "Tree", "Tab", "Menu", "MenuBar", "ToolBar")

function Find-Window([int]$ProcessId) {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        $condition = if ($ProcessId) {
            New-Object System.Windows.Automation.PropertyCondition($AE::ProcessIdProperty, $ProcessId)
        } else { [System.Windows.Automation.Condition]::TrueCondition }
        foreach ($window in $AE::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)) {
            $name = $window.Current.Name
            if ($WindowTitle -and $name -notlike "*$WindowTitle*") { continue }
            if (-not $ProcessId -and -not $WindowTitle) { continue }
            return $window
        }
        Start-Sleep -Milliseconds 250   # polling a GUI that is still starting; no event to wait on
    }
    throw "No window found within $WaitSeconds s (process id: $ProcessId, title: $WindowTitle)"
}

# The managed client reports most classic controls as panes, so the window class tells what they are.
$InteractiveClass = 'EDIT|BUTTON|COMBOBOX|RICHEDIT|SysDateTimePick|SysTabControl|LISTBOX|SysListView|SysTreeView|msctls_updown|ToolbarWindow'

function Grade($type, $id, $name, $class) {
    if ($Interactive -notcontains $type -and $class -notmatch $InteractiveClass) { return "" }
    $stableId = $id -and -not ($id -match '^\d+$' -and $class -like 'WindowsForms10*')   # WinForms numeric ids are window handles
    if ($stableId) { return "good" }
    if ($name) { return "fair" }
    return "poor"
}

$rows = New-Object System.Collections.Generic.List[object]
function Walk($element, [int]$depth, [string]$path) {
    if ($depth -gt $MaxDepth) { return }
    $c = $element.Current
    $type = $c.ControlType.ProgrammaticName -replace '^ControlType\.', ''
    $children = $element.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    $row = [pscustomobject]@{
        Depth        = $depth
        ControlType  = $type
        AutomationId = $c.AutomationId
        Name         = $c.Name
        ClassName    = $c.ClassName
        Enabled      = $c.IsEnabled
        Children     = $children.Count
        Grade        = Grade $type $c.AutomationId $c.Name $c.ClassName
        Path         = $path
    }
    if (-not $row.Grade -and $type -eq "Pane" -and $children.Count -eq 0 -and -not $c.Name) { $row.Grade = "opaque" }
    $rows.Add($row)
    $index = @{}
    foreach ($child in $children) {
        $childType = $child.Current.ControlType.ProgrammaticName -replace '^ControlType\.', ''
        $index[$childType] = 1 + [int]$index[$childType]
        Walk $child ($depth + 1) ("$path/$childType[" + $index[$childType] + "]")
    }
}

$process = $null
if ($Exe) {
    $exePath = (Resolve-Path $Exe).Path
    $process = if ($Arguments) { Start-Process -FilePath $exePath -ArgumentList $Arguments -PassThru } else { Start-Process -FilePath $exePath -PassThru }
    $window = Find-Window $process.Id
} elseif ($ProcessName) {
    $found = Get-Process -Name $ProcessName -ErrorAction Stop | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if (-not $found) { throw "Process '$ProcessName' has no window" }
    $window = Find-Window $found.Id
} elseif ($WindowTitle) {
    $window = Find-Window 0
} else {
    throw "Give -Exe, -ProcessName or -WindowTitle"
}

try {
    Start-Sleep -Milliseconds 500   # let the first dialog finish laying out
    Walk $window 0 ("/Window")
} finally {
    if ($process -and -not $KeepOpen -and -not $process.HasExited) { $process.Kill() }
}

if (-not $Quiet) {
    $rows | ForEach-Object {
        $indent = "  " * $_.Depth
        $grade = if ($_.Grade) { "[$($_.Grade)]" } else { "" }
        "{0}{1,-8} {2}  id='{3}' name='{4}' class='{5}'" -f $indent, $grade, $_.ControlType, $_.AutomationId, $_.Name, $_.ClassName
    }
}

$graded = @($rows | Where-Object { $_.Grade -in @("good", "fair", "poor") })
$total = [math]::Max(1, $graded.Count)
$summary = [ordered]@{
    window              = $window.Current.Name
    elements            = $rows.Count
    interactive         = $graded.Count
    good_automation_id  = @($graded | Where-Object Grade -eq "good").Count
    fair_name_only      = @($graded | Where-Object Grade -eq "fair").Count
    poor_unlocatable    = @($graded | Where-Object Grade -eq "poor").Count
    opaque_panes        = @($rows | Where-Object Grade -eq "opaque").Count
}
$summary.readiness_percent = [math]::Round(100 * ($summary.good_automation_id + 0.5 * $summary.fair_name_only) / $total)
""
"NovaWindows view - UI Automation readiness: {0}%  ({1} interactive: {2} good, {3} name only, {4} unlocatable; {5} opaque pane(s))" -f `
    $summary.readiness_percent, $summary.interactive, $summary.good_automation_id, $summary.fair_name_only,
    $summary.poor_unlocatable, $summary.opaque_panes

if ($Csv) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $Csv) | Out-Null
    $rows | Export-Csv -NoTypeInformation -Encoding UTF8 $Csv
}
if ($Json) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $Json) | Out-Null
    [ordered]@{ summary = $summary; elements = $rows } | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $Json
}
