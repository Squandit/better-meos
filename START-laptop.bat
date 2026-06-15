@echo off
REM Laptop launcher: uses the venv stored OUTSIDE OneDrive so it never syncs
REM back to the desktop PC. The PC keeps using venv\ + START.bat as normal.
set "LAPTOP_VENV=%USERPROFILE%\.better-meos-venv"
if not exist "%LAPTOP_VENV%\Scripts\python.exe" (
  echo Laptop venv not found at %LAPTOP_VENV%
  echo Create it with:
  echo   python -m venv "%LAPTOP_VENV%"
  echo   "%LAPTOP_VENV%\Scripts\python" -m pip install -r requirements.txt
  exit /b 1
)
"%LAPTOP_VENV%\Scripts\python.exe" launcher.py %*
