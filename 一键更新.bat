@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0runtime\python.exe" (
  echo Bundled Python runtime not found. Please install the portable runtime first.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" "%~dp0updater\update.py" --apply
set "UPDATE_RESULT=%ERRORLEVEL%"
pause
exit /b %UPDATE_RESULT%
