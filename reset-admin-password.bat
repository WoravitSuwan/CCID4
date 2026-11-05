@echo off
rem Forgot the admin password / cannot log in: set a new one (the system must be running: start-docker.bat).
setlocal
cd /d "%~dp0"
chcp 65001 >nul
docker compose exec web python manage.py reset_admin
if errorlevel 1 (
  echo Could not reach the system. Start it first with start-docker.bat, then run this again.
  pause
  exit /b 1
)
echo.
echo Log in with the user name and password above.
pause
