param(
    [string]$TaskName = "HotelOTACollector",
    [string]$PythonPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "runtime\python.exe"),
    [string]$RunnerPath = (Join-Path $PSScriptRoot "runner.py"),
    [int]$IntervalMinutes = 0,
    [string]$SettingsPath = (Join-Path $PSScriptRoot "config\settings.json")
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "task_scheduler_common.ps1")

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "Runner not found: $RunnerPath"
}
$clockOptions = @{}
if ($IntervalMinutes -le 0) {
    $IntervalMinutes = 60
    $clockOptions.FirstRunAt = Get-CollectionFirstRun -SettingsPath $SettingsPath
    $clockOptions.RunImmediately = $true
}

Install-RepeatingTask `
    -TaskName $TaskName `
    -ExecutablePath $PythonPath `
    -Arguments "`"$RunnerPath`" run-once" `
    -WorkingDirectory (Split-Path -Parent $RunnerPath) `
    -IntervalMinutes $IntervalMinutes @clockOptions

Write-Host "Scheduled task installed: $TaskName, interval=${IntervalMinutes}min"
