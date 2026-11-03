@echo off
setlocal
cd /d "%~dp0"
title MEDKids Rap Studio
echo === MEDKids Rap Studio ===

rem ---- find Python 3.10+ ---------------------------------------------------
set "PY="
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)

rem ---- one-time setup --------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo First run: creating a virtual environment ...
  %PY% -m venv .venv || goto :fail
)
set "VPY=.venv\Scripts\python.exe"
echo Checking dependencies ...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
if not exist ".env" copy /y ".env.example" ".env" >nul

rem ---- local mode: this computer only, no API keys needed --------------------
set "APP_ENV=development"
set "AUTH_MODE=dev"
set "DEV_AUTH_ENABLED=true"

"%VPY%" scripts\init_db.py || goto :fail

echo.
echo MEDKids Rap Studio is running at http://127.0.0.1:8000
echo Keep this window open while you use the app. Close it (or press Ctrl+C) to stop.
echo.
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000/"
"%VPY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
goto :eof

:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
