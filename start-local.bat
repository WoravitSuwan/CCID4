@echo off
rem Summons system - start on this computer (no Docker needed).
rem Double-click this file. First run installs everything; later runs just start.
rem   start-local.bat demo   = also load fictional sample data
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PY="

py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=python"

if not defined PY (
  echo Python 3.11 or newer was not found.
  where winget >nul 2>nul
  if not errorlevel 1 (
    echo Installing Python 3.12 with winget ...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo.
    echo Python installed. Close this window and double-click start-local.bat again.
  ) else (
    echo Please install Python 3.12 from https://www.python.org/downloads/
    echo  - tick "Add python.exe to PATH" in the installer
    echo Then double-click start-local.bat again.
  )
  pause
  exit /b 1
)

%PY% tools\run_local.py %*
if errorlevel 1 pause
