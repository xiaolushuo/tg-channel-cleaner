@echo off
cd /d "%~dp0"
title TG Leave Tool v3.20.7 (web)

if not exist "api\.venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found: api\.venv
  pause
  exit /b 1
)
if not exist "ui\dist\index.html" (
  echo [ERROR] Frontend not built: ui\dist missing.
  echo Build it first:  cd ui ^&^& npm install ^&^& npm run build
  pause
  exit /b 1
)

echo ============================================
echo   TG Leave Tool v3.20.7 (web)
echo   http://127.0.0.1:8787
echo ============================================
start "" "http://127.0.0.1:8787"
"api\.venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8787
pause
