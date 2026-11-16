@echo off
rem End-to-end smoke of Policy Desk through native UI Automation, in one or more UI releases.
rem
rem   tools\smoke.bat              all four releases, v1 to v4
rem   tools\smoke.bat v2           one release
rem
rem Builds the app and the tools first. Needs an unlocked, interactive desktop (it moves the mouse), so it is not
rem run in CI. Exit code 0 = every release passed.
setlocal
set "HERE=%~dp0"
call "%HERE%..\app\build.bat" >nul
if errorlevel 1 exit /b 1
call "%HERE%build_tools.bat" >nul
if errorlevel 1 exit /b 1
set "RELEASES=%*"
if "%RELEASES%"=="" set "RELEASES=v1 v2 v3 v4"
set "FAILED=0"
for %%r in (%RELEASES%) do (
    "%HERE%bin\PolicyDeskSmoke.exe" %%r "%HERE%..\app\bin\PolicyDesk.exe"
    if errorlevel 1 set "FAILED=1"
)
exit /b %FAILED%
