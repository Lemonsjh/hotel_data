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
        [int]$IntervalMinutes,
        [System.Management.Automation.PSCredential]$Credential
    )

    if ($IntervalMinutes -le 0) {
        throw "Task interval must be greater than zero"
    }

    $action = New-ScheduledTaskAction `
        -Execute $ExecutablePath `
        -Argument $Arguments `
        -WorkingDirectory $WorkingDirectory
    $startupTrigger = New-ScheduledTaskTrigger -AtStartup `
        -RandomDelay (New-TimeSpan -Minutes 1)
    $repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $taskSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 2)

    if ($null -eq $Credential) {
        $defaultUser = "$env:USERDOMAIN\$env:USERNAME"
        $Credential = Get-Credential `
            -UserName $defaultUser `
            -Message "Enter the Windows password used to run $TaskName when nobody is logged on."
    }
    if ($null -eq $Credential) {
        throw "Windows credentials are required to install $TaskName"
    }

    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential.Password)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger @($startupTrigger, $repeatTrigger) `
            -Settings $taskSettings `
            -User $Credential.UserName `
            -Password $plainPassword `
            -RunLevel Highest `
            -Force | Out-Null
    }
    finally {
        $plainPassword = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
}
