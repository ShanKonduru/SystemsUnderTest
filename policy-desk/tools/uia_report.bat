@echo off
rem UI Automation readiness report, both views, for a Windows desktop app:
rem   1. native UIA3 (Inspect.exe, WinAppDriver / Appium "Windows" driver, FlaUI)   - tools\bin\UiaReport.exe
rem   2. NovaWindows (managed UI Automation in powershell.exe)                       - tools\uia_report_nova.ps1
rem
rem   tools\uia_report.bat -Exe app\bin\PolicyDesk.exe -Arguments "--release v2"
rem   tools\uia_report.bat -ProcessName PolicyDesk          (attach to a running app, e.g. after signing in)
rem   tools\uia_report.bat -WindowTitle "Harbor Mutual Policy Desk 3.0" -Quiet (summaries only)
rem
rem Same options for both: -Exe -Arguments -ProcessName -WindowTitle -WaitSeconds -MaxDepth -KeepOpen -Quiet.
rem For -Csv / -Json files, run one view on its own (both would write the same file).
rem With -Exe the app is started once per view and closed afterwards.
setlocal
set "HERE=%~dp0"
if not exist "%HERE%bin\UiaReport.exe" (
    call "%HERE%build_tools.bat"
    if errorlevel 1 exit /b 1
)
echo ==== Native UIA3 view ==========================================================
"%HERE%bin\UiaReport.exe" %*
set "NATIVE=%ERRORLEVEL%"
echo.
echo ==== NovaWindows view ==========================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%uia_report_nova.ps1" %*
set "NOVA=%ERRORLEVEL%"
if not "%NATIVE%"=="0" exit /b %NATIVE%
exit /b %NOVA%
