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

function Get-CollectionFirstRun {
    param([string]$SettingsPath, [datetime]$Now = (Get-Date))
    $minute = 0
    if (Test-Path -LiteralPath $SettingsPath) {
        $settings = Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -ne $settings.service.collection_minute) {
            $configured = $settings.service.collection_minute
            if ($configured -isnot [int] -and $configured -isnot [long]) { throw "Invalid collection minute" }
            $minute = [int]$configured
        }
    }
    if ($minute -notin @(0, 30, 50)) { throw "Collection minute must be 0, 30 or 50" }
    $target = $Now.Date.AddHours($Now.Hour).AddMinutes($minute)
    if ($target -le $Now) { $target = $target.AddHours(1) }
    return $target
}

function Install-RepeatingTask {
    param(
        [string]$TaskName,
        [string]$ExecutablePath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [int]$IntervalMinutes,
        [System.Management.Automation.PSCredential]$Credential,
        [datetime]$FirstRunAt = [datetime]::MinValue,
        [switch]$RunImmediately
    )

    if ($IntervalMinutes -le 0) {
        throw "Task interval must be greater than zero"
    }

    $action = New-ScheduledTaskAction `
        -Execute $ExecutablePath `
        -Argument $Arguments `
        -WorkingDirectory $WorkingDirectory
    $fixedClock = $FirstRunAt -ne [datetime]::MinValue
    if (-not $fixedClock) { $FirstRunAt = (Get-Date).AddMinutes(1) }
    $repeatTrigger = New-ScheduledTaskTrigger -Once -At $FirstRunAt `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $triggers = @($repeatTrigger)
    $options = @{ AllowStartIfOnBatteries = $true; DontStopIfGoingOnBatteries = $true; MultipleInstances = 'IgnoreNew' }
    if (-not $fixedClock) {
        $startupTrigger = New-ScheduledTaskTrigger -AtStartup -RandomDelay (New-TimeSpan -Minutes 1)
        $triggers = @($startupTrigger, $repeatTrigger)
        $options.StartWhenAvailable = $true
        $options.RestartCount = 3
        $options.RestartInterval = New-TimeSpan -Minutes 2
    }
    $taskSettings = New-ScheduledTaskSettingsSet @options

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
            -Trigger $triggers `
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
    if ($RunImmediately) { Start-ScheduledTask -TaskName $TaskName }
}
