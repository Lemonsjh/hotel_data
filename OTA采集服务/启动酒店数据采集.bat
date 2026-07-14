@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0service_control.ps1" -Action Start
if errorlevel 1 pause
