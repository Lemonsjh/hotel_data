$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$common = Get-ChildItem -LiteralPath $root -Directory | ForEach-Object {
    Join-Path $_.FullName 'task_scheduler_common.ps1'
} | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
. $common
$config = Join-Path $root ('exports/clock-test-' + [guid]::NewGuid().ToString('N') + '.json')

function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory) return 'test-action' }
function New-ScheduledTaskTrigger {
    param([switch]$Once, [datetime]$At, [timespan]$RepetitionInterval, [timespan]$RepetitionDuration,
          [switch]$AtStartup, [timespan]$RandomDelay)
    return [pscustomobject]@{ At = $At; Interval = $RepetitionInterval; Startup = $AtStartup.IsPresent }
}
function New-ScheduledTaskSettingsSet {
    param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, $MultipleInstances,
          [switch]$StartWhenAvailable, [int]$RestartCount, [timespan]$RestartInterval)
    $script:lastSettings = @{}
    foreach ($key in $PSBoundParameters.Keys) { $script:lastSettings[$key] = $PSBoundParameters[$key] }
    return 'test-settings'
}
function Register-ScheduledTask {
    param($TaskName, $Action, $Trigger, $Settings, $User, $Password, $RunLevel, [switch]$Force)
    $script:lastTriggers = $Trigger
}
$script:immediateStarts = 0
function Start-ScheduledTask { param($TaskName) $script:immediateStarts++ }

try {
    foreach ($minute in @(0, 30, 50)) {
        @{ service = @{ collection_minute = $minute } } | ConvertTo-Json | Set-Content -LiteralPath $config -Encoding UTF8
        $now = [datetime]'2026-11-06T10:20:00'
        $next = Get-CollectionFirstRun -SettingsPath $config -Now $now
        $expected = $now.Date.AddHours($(if ($minute -eq 0) { 11 } else { 10 })).AddMinutes($minute)
        if ($next -ne $expected) { throw 'Wrong first run' }
    }
    @{ service = @{ collection_minute = 50 } } | ConvertTo-Json | Set-Content -LiteralPath $config -Encoding UTF8
    if ((Get-CollectionFirstRun -SettingsPath $config -Now ([datetime]'2026-11-06T10:50:00')).Hour -ne 11) {
        throw 'Exact boundary must wait for next hour'
    }
    $credential = [pscredential]::new('test-only', (ConvertTo-SecureString 'unused-test-only' -AsPlainText -Force))
    Install-RepeatingTask -TaskName 'fake' -ExecutablePath 'fake.exe' -Arguments 'fake' -WorkingDirectory $root `
        -IntervalMinutes 60 -Credential $credential -FirstRunAt ([datetime]'2026-11-06T11:00:00') -RunImmediately
    if ($script:immediateStarts -ne 1) { throw 'Fixed-clock collection must start once immediately' }
    if ($script:lastTriggers.Count -ne 1 -or $script:lastTriggers[0].Interval.TotalMinutes -ne 60 `
            -or $script:lastTriggers[0].At -ne [datetime]'2026-11-06T11:00:00') { throw 'Wrong fixed-clock trigger' }
    if ($script:lastSettings.ContainsKey('StartWhenAvailable') -or $script:lastSettings.ContainsKey('RestartCount')) {
        throw 'Fixed clock must not catch up or restart outside its slots'
    }
    if ($script:lastSettings.MultipleInstances -ne 'IgnoreNew') { throw 'Overlapping task instances allowed' }
    Install-RepeatingTask -TaskName 'fake' -ExecutablePath 'fake.exe' -Arguments 'fake' -WorkingDirectory $root `
        -IntervalMinutes 5 -Credential $credential
    if ($script:lastTriggers.Count -ne 2 -or $script:lastSettings.RestartCount -ne 3) { throw 'Legacy task behavior changed' }
    if ($script:immediateStarts -ne 1) { throw 'Other schedulers must not gain an immediate run' }
    Write-Output 'PowerShell fixed-clock and legacy-task validation passed; no tasks registered.'
}
finally {
    if (Test-Path -LiteralPath $config) { Remove-Item -LiteralPath $config -Force }
}
