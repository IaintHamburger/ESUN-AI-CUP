@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_schedule.ps1"
if errorlevel 1 (
  echo Schedule setup failed. Check the error above.
  pause
  exit /b 2
)
pause
