@echo off
setlocal

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator permission...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

set TASK_NAME=HotelOTAPriceExecutor
set PYTHON_PATH=%~dp0..\runtime\python.exe
if not exist "%PYTHON_PATH%" for %%I in (python.exe) do set PYTHON_PATH=%%~$PATH:I
set EXECUTOR_PATH=%~dp0price_executor.py
set INTERVAL_MINUTES=5

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0price_scheduler_task.ps1" -TaskName "%TASK_NAME%" -PythonPath "%PYTHON_PATH%" -ExecutorPath "%EXECUTOR_PATH%" -IntervalMinutes %INTERVAL_MINUTES%
if errorlevel 1 (
  echo Failed to install price executor scheduled task.
  exit /b 1
)
echo Installed scheduled task: %TASK_NAME%
endlocal
