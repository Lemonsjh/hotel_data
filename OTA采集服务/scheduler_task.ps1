param(
    [string]$TaskName = "HotelOTACollector",
    [string]$PythonPath = "",
    [string]$RunnerPath = "",
    [int]$IntervalMinutes = 0,
    [string]$SettingsPath = ""
)

$ErrorActionPreference = "Stop"
Write-Host "不再创建计划任务 HotelOTACollector。开机只保留当前用户登录时启动。"
& (Join-Path $PSScriptRoot "install_logon_task.ps1")
