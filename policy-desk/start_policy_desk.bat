@echo off
rem Build and launch Harbor Mutual Policy Desk. Every option except --no-build goes to the app:
rem
rem   start_policy_desk.bat                              build, then launch (release v1, data in policy-desk\data)
rem   start_policy_desk.bat --reset                      start from the seed data
rem   start_policy_desk.bat --release v3 --drift 10      UI release v3, every control renamed every 10 s
rem   start_policy_desk.bat --no-build --help            the app's options
rem
rem Sign in with any user name; the password is --password, else POLICYDESK_PASSWORD, else "harbor".
rem The build takes about two seconds, so it always runs: the exe never lags behind the source.
setlocal
set "HERE=%~dp0"
set "EXE=%HERE%app\bin\PolicyDesk.exe"
set "BUILD=1"
set "ARGS="

:args
if "%~1"=="" goto :start
if /I "%~1"=="--no-build" (set "BUILD=0") else (set ARGS=%ARGS% %1)
shift
goto :args

:start
if "%BUILD%"=="1" (
    call "%HERE%app\build.bat"
    if errorlevel 1 exit /b 1
)
if not exist "%EXE%" (
    echo [start] %EXE% not found - run without --no-build. 1>&2
    exit /b 1
)
start "" "%EXE%" %ARGS%
echo [start] Policy Desk launched. Sign in with any user name; password: --password, %%POLICYDESK_PASSWORD%% or "harbor".
