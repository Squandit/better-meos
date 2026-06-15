@echo off
REM ===========================================================================
REM  Punchcard / better-meos launcher
REM  Double-click this file to run the program. It starts the local server and
REM  opens your browser at the event-selection page.
REM
REM  Admin console : http://punchcard/     (or http://127.0.0.1/  -- port 80)
REM  Entry+results : http://<this-PC>:8800/ (share this one with phones)
REM  Stop the server: close this window, or press Ctrl+C.
REM ===========================================================================
title Punchcard
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo   Python was not found, and no venv exists in this folder.
        echo   Create it once with:
        echo       python -m venv venv
        echo       venv\Scripts\python -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
    set "PY=python"
)

echo Starting Punchcard...
"%PY%" launcher.py

REM Keep the window open if the server stops or errors, so you can read why.
echo.
echo Punchcard has stopped.
pause
