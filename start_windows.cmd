@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo === Pusyuu IP Watcher - Windows startup ===
echo.

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto :python_found
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if defined PYTHON_CMD goto :python_found

echo [ERROR] Python 3 was not found.
echo.
echo Python is required to run this application.
echo.
choice /C YN /N /M "Install Python 3 automatically with winget? [Y/N]: "
if errorlevel 2 goto :manual_install
if errorlevel 1 goto :install_python

:install_python
echo.
echo Installing Python 3.13 using Windows winget...
where winget >nul 2>&1
if errorlevel 1 goto :no_winget
winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :install_failed

rem winget changes PATH only for new processes. Look for the installed executable.
for /f "delims=" %%P in ('where /r "%LocalAppData%\Programs\Python" python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if defined PYTHON_EXE (
  set "PYTHON_CMD=\"!PYTHON_EXE!\""
  goto :python_found
)

echo.
echo Python was installed, but this window cannot see the new PATH yet.
echo Please close this window and run start_windows.cmd again.
pause
exit /b 0

:no_winget
echo.
echo [ERROR] winget is not available on this Windows installation.
goto :manual_install

:install_failed
echo.
echo [ERROR] Python installation via winget failed.
goto :manual_install

:manual_install
echo.
echo Please install Python 3 manually from:
echo   https://www.python.org/downloads/windows/
echo.
echo During installation, enable "Add python.exe to PATH".
echo Then reopen this window and run start_windows.cmd again.
echo.
pause
exit /b 1

:python_found
echo Using: %PYTHON_CMD%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :failed
)

if not exist ".env" if exist ".env.example" (
    echo.
    echo Creating .env from .env.example...
    copy /Y ".env.example" ".env" >nul
)

echo Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Starting server...
echo Open http://127.0.0.1:8000/ in your browser.
echo The setup/operation guide is available at http://127.0.0.1:8000/guide
echo Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" main.py --host 127.0.0.1 --port 8000
exit /b %errorlevel%

:failed
echo.
echo [ERROR] Startup failed.
echo Read the error above. If this is a Python/package error, run check_python.cmd for diagnostics.
echo.
pause
exit /b 1
