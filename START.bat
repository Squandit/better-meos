@echo off
REM Start Control for the operator (development / non-exe launch).
REM Double-click this, or build the standalone exe with: pyinstaller control-orienteering.spec
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" launcher.py
) else (
    python launcher.py
)
pause
