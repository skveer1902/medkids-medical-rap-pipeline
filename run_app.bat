@echo off
setlocal
cd /d "%~dp0"
title MEDKids Medical Rap Pipeline
echo === MEDKids Medical Rap Pipeline ===

set "PY="
rem Prefer the newest Python 3.10+ via the Windows py launcher, then fall back to "python" on PATH.
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10 or newer was not found. Installed Pythons:
  py -0p 2>nul
  python --version 2>nul
  echo.
  echo Install Python 3.12 from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
  echo or run:  winget install -e --id Python.Python.3.12
  echo then double-click run_app.bat again.
  pause
  exit /b 1
)
echo Using: %PY%
%PY% --version

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)
set "VPY=.venv\Scripts\python.exe"

echo Installing / checking dependencies (the first run takes a minute) ...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :fail

if not exist ".env" (
  "%VPY%" scripts\make_local_env.py
  if errorlevel 1 goto :fail
)
if not exist ".venv\tests_passed.txt" (
  echo Running the test suite once to verify this setup ...
  "%VPY%" -m pytest -q -p no:cacheprovider
  if errorlevel 1 goto :fail
  echo ok> ".venv\tests_passed.txt"
)
"%VPY%" scripts\init_db.py
if errorlevel 1 goto :fail

echo.
echo Starting MEDKids Rap Studio at http://127.0.0.1:8000  (API docs: http://127.0.0.1:8000/docs)
echo Paste the keys from the API_KEYS line of .env into the API keys box in the browser. Press Ctrl+C to stop the server.
echo.
start "" cmd /c "timeout /t 5 >nul & start http://127.0.0.1:8000/"
"%VPY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
goto :eof

:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
