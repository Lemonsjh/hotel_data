@echo off
setlocal

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator permission...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

set TASK_NAME=HotelOTACollector
set PYTHON_PATH=%~dp0..\runtime\python.exe
if not exist "%PYTHON_PATH%" for %%I in (python.exe) do set PYTHON_PATH=%%~$PATH:I
set RUNNER_PATH=%~dp0runner.py
set INTERVAL_MINUTES=60

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scheduler_task.ps1" -TaskName "%TASK_NAME%" -PythonPath "%PYTHON_PATH%" -RunnerPath "%RUNNER_PATH%" -IntervalMinutes %INTERVAL_MINUTES%
if errorlevel 1 (
  echo Failed to install scheduled task.
  exit /b 1
)
echo Installed scheduled task: %TASK_NAME%
endlocal
