param(
    [string]$TaskName = "HotelOTAPriceExecutor",
    [string]$PythonPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "runtime\python.exe"),
    [string]$ExecutorPath = (Join-Path $PSScriptRoot "price_executor.py"),
    [int]$IntervalMinutes = 0,
    [string]$SettingsPath = (Join-Path $PSScriptRoot "config\settings.json")
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "task_scheduler_common.ps1")

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $ExecutorPath)) {
    throw "Price executor not found: $ExecutorPath"
}
$IntervalMinutes = Resolve-TaskInterval `
    -RequestedInterval $IntervalMinutes `
    -DefaultInterval 5 `
    -SettingsPath $SettingsPath `
    -PropertyPath @("price_scheduler", "interval_minutes")

Install-RepeatingTask `
    -TaskName $TaskName `
    -ExecutablePath $PythonPath `
    -Arguments "`"$ExecutorPath`"" `
    -WorkingDirectory (Split-Path -Parent $ExecutorPath) `
    -IntervalMinutes $IntervalMinutes

Write-Host "Scheduled task installed: $TaskName, interval=${IntervalMinutes}min"
