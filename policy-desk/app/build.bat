@echo off
rem Build Harbor Mutual Policy Desk with the C# compiler that ships with Windows (.NET Framework 4.x); no SDK needed.
rem Output: app\bin\PolicyDesk.exe (the desktop app), app\bin\policydesk-cli.exe (console build for --self-test and
rem --locator-map) and app\bin\releases.json (the UI release rules both read).
setlocal
set "HERE=%~dp0"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
    echo [build] .NET Framework 4.x C# compiler not found. 1>&2
    exit /b 1
)
if not exist "%HERE%bin" mkdir "%HERE%bin"
set "REFS=/r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll"
set "SRC="%HERE%Program.cs" "%HERE%Domain.cs" "%HERE%Releases.cs" "%HERE%Forms.cs" "%HERE%SelfTest.cs""
"%CSC%" /nologo /target:winexe /optimize+ /warnaserror+ /out:"%HERE%bin\PolicyDesk.exe" %REFS% %SRC%
if errorlevel 1 exit /b 1
"%CSC%" /nologo /target:exe /optimize+ /warnaserror+ /define:CONSOLE /out:"%HERE%bin\policydesk-cli.exe" %REFS% %SRC%
if errorlevel 1 exit /b 1
copy /y "%HERE%releases.json" "%HERE%bin\releases.json" >nul
if errorlevel 1 exit /b 1
echo [build] %HERE%bin\PolicyDesk.exe
echo [build] %HERE%bin\policydesk-cli.exe
