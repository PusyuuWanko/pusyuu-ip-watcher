@echo off
setlocal
cd /d "%~dp0"
echo === Pusyuu IP Watcher - Python diagnostics ===
echo.
where py >nul 2>&1
if not errorlevel 1 (
  echo [OK] Python launcher: py
  py --version
) else echo [NG] Python launcher: py is not available.
where python >nul 2>&1
if not errorlevel 1 (
  echo [OK] Python command: python
  python --version
) else echo [NG] Python command: python is not available.
if exist ".venv\Scripts\python.exe" (
  echo [OK] Virtual environment exists.
  ".venv\Scripts\python.exe" --version
) else echo [INFO] Virtual environment does not exist yet. start_windows.cmd will create it.
where winget >nul 2>&1
if not errorlevel 1 (echo [OK] winget is available: automatic Python installation is possible.) else echo [WARN] winget is not available: install Python manually.
echo.
echo Recommended action:
echo   1. Run start_windows.cmd.
echo   2. If asked whether to install Python, press Y.
echo   3. If automatic installation is unavailable, install Python from https://www.python.org/downloads/windows/
pause
