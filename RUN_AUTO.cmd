@echo off
cd /d "%~dp0"
if not exist runtime mkdir runtime
if not exist .venv-simple\Scripts\python.exe exit /b 2
.venv-simple\Scripts\python.exe run_simple.py auto >> runtime\schedule.log 2>&1
exit /b %errorlevel%
