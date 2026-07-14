@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_desktop_shortcuts.ps1"
if errorlevel 1 pause
