@echo off
if not exist "%~dp0frontend\node_modules" (
  echo Frontend dependencies missing. Run npm ci inside frontend first.
  pause
  exit /b 1
)
cd /d "%~dp0frontend"
call npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
pause
