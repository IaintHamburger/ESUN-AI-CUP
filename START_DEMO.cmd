@echo off
cd /d "%~dp0"
if not exist .venv-simple\Scripts\python.exe (
  py -3 -m venv .venv-simple
  if errorlevel 1 goto :error
)
.venv-simple\Scripts\python.exe -m pip install -r requirements-simple.txt
if errorlevel 1 goto :error
.venv-simple\Scripts\python.exe run_simple.py demo
if errorlevel 1 goto :error
echo Demo completed. Read output\demo\validation.json
pause
exit /b 0
:error
echo Setup or demo failed. Install Python 3.10+ and check the message above.
pause
exit /b 1
