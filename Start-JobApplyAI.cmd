@echo off
title JobApplyAI
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Python environment missing. Complete the README Quick start first.
  pause
  exit /b 1
)
if not exist "%~dp0frontend\node_modules" (
  echo Frontend dependencies missing. Run npm ci inside frontend first.
  pause
  exit /b 1
)
echo Starting JobApplyAI...
start "JobApplyAI backend" cmd /k ""%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir "%~dp0backend""
timeout /t 4 /nobreak >nul
start "JobApplyAI frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort"
timeout /t 6 /nobreak >nul
start http://127.0.0.1:5173
echo.
echo Backend  http://127.0.0.1:8000  (API docs at /docs)
echo Frontend http://127.0.0.1:5173
echo.
echo Close the two terminal windows to stop.
pause
