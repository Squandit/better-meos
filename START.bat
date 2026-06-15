@echo off
REM Start better-meos for the operator (development / non-exe launch).
REM Double-click this, or build the standalone exe with: pyinstaller better-meos.spec
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" launcher.py
) else (
    python launcher.py
)
pause
