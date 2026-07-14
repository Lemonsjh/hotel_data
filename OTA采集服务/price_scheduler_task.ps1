param(
    [string]$TaskName = "HotelOTAPriceExecutor",
    [string]$PythonPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "runtime\python.exe"),
    [string]$ExecutorPath = (Join-Path $PSScriptRoot "price_executor.py"),
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $ExecutorPath)) {
    throw "Price executor not found: $ExecutorPath"
}

$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "`"$ExecutorPath`"" `
    -WorkingDirectory (Split-Path -Parent $ExecutorPath)

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName, interval=${IntervalMinutes}min"
