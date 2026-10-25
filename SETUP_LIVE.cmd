@echo off
cd /d "%~dp0"
if not exist .venv-simple\Scripts\python.exe (
  echo Run START_DEMO.cmd first to install the Python environment.
  pause
  exit /b 2
)
.venv-simple\Scripts\python.exe setup_simple.py
if errorlevel 1 exit /b 2
.venv-simple\Scripts\python.exe run_simple.py doctor
pause
