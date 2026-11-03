@echo off
rem Summons system - start with Docker Desktop (PostgreSQL + Redis + LibreOffice inside containers).
setlocal
cd /d "%~dp0"
chcp 65001 >nul
docker version >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not running. Start Docker Desktop, wait for "Engine running", then try again.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_env.ps1
if errorlevel 1 ( pause & exit /b 1 )
set PORT=8000
for /f "tokens=2 delims==" %%p in ('findstr /b "WEB_PORT=" .env 2^>nul') do set PORT=%%p
echo Building and starting containers (first time takes several minutes) ...
docker compose up -d --build
if errorlevel 1 ( pause & exit /b 1 )
echo Waiting for the web server ...
powershell -NoProfile -Command "for($i=0;$i -lt 120;$i++){try{Invoke-WebRequest -UseBasicParsing http://localhost:%PORT%/healthz | Out-Null; exit 0}catch{Start-Sleep 2}}; exit 1"
if errorlevel 1 (
  echo The web server did not start. Showing logs:
  docker compose logs --tail 50 web
  pause
  exit /b 1
)
type data\admin-password.txt 2>nul
start "" http://localhost:%PORT%
echo System is running at http://localhost:%PORT%   (stop: docker compose down)
pause
