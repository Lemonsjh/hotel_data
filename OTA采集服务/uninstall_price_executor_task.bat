@echo off
setlocal

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator permission...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

set TASK_NAME=HotelOTAPriceExecutor
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
  echo Failed to uninstall price executor task or task does not exist.
  exit /b 1
)
echo Uninstalled scheduled task: %TASK_NAME%
endlocal
