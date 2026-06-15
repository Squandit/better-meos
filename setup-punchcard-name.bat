@echo off
REM ===========================================================================
REM  One-time setup: make http://punchcard/ resolve to this PC.
REM  Double-click this file once and approve the admin (UAC) prompt. It adds
REM  "127.0.0.1 punchcard" to the Windows hosts file. Re-running is harmless.
REM ===========================================================================
title Punchcard name setup

REM --- Re-launch elevated if we're not already admin ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

set "HOSTS=%SystemRoot%\System32\drivers\etc\hosts"

findstr /I /C:"punchcard" "%HOSTS%" >nul 2>&1
if %errorlevel%==0 (
    echo "punchcard" is already in the hosts file. Nothing to do.
) else (
    >>"%HOSTS%" echo 127.0.0.1    punchcard
    echo Added: 127.0.0.1 punchcard
)

ipconfig /flushdns >nul 2>&1
echo.
echo Done. Start Punchcard (START.bat), then open:  http://punchcard/
echo.
pause
