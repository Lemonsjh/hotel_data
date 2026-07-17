function Resolve-TaskInterval {
    param(
        [int]$RequestedInterval,
        [int]$DefaultInterval,
        [string]$SettingsPath,
        [string[]]$PropertyPath
    )

    if ($RequestedInterval -gt 0) {
        return $RequestedInterval
    }
    if ($DefaultInterval -le 0) {
        throw "Default interval must be greater than zero"
    }
    if (-not (Test-Path -LiteralPath $SettingsPath)) {
        return $DefaultInterval
    }

    try {
        $value = Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($segment in $PropertyPath) {
            $property = $value.PSObject.Properties[$segment]
            if ($null -eq $property) {
                return $DefaultInterval
            }
            $value = $property.Value
        }

        $configuredInterval = 0
        if ([int]::TryParse([string]$value, [ref]$configuredInterval) -and $configuredInterval -gt 0) {
            return $configuredInterval
        }
    }
    catch {
        Write-Warning "Unable to read task interval from $SettingsPath; using ${DefaultInterval}min. $($_.Exception.Message)"
    }
    return $DefaultInterval
}

function Install-RepeatingTask {
    param(
        [string]$TaskName,
        [string]$ExecutablePath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [int]$IntervalMinutes
    )

    if ($IntervalMinutes -le 0) {
        throw "Task interval must be greater than zero"
    }

    $action = New-ScheduledTaskAction `
        -Execute $ExecutablePath `
        -Argument $Arguments `
        -WorkingDirectory $WorkingDirectory
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME `
        -LogonType Interactive `
        -RunLevel Limited
    $taskSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $taskSettings `
        -Force | Out-Null
}
