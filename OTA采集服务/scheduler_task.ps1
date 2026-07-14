param(
    [string]$TaskName = "HotelOTACollector",
    [string]$PythonPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "runtime\python.exe"),
    [string]$RunnerPath = (Join-Path $PSScriptRoot "runner.py"),
    [int]$IntervalMinutes = 60
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "Runner not found: $RunnerPath"
}

$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "`"$RunnerPath`" run-once" `
    -WorkingDirectory (Split-Path -Parent $RunnerPath)

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName, interval=${IntervalMinutes}min"
