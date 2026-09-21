@echo off
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Python environment missing. Complete the README Quick start first.
  pause
  exit /b 1
)
cd /d "%~dp0backend"
"%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
pause
