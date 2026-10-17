param(
    [Parameter(Mandatory=$true)][string]$Root,
    [Parameter(Mandatory=$true)][string]$ServiceDir,
    [Parameter(Mandatory=$true)][string]$Snapshot,
    [ValidateSet('Inspect','Suspend','Panel','Schedulers','Undo')][string]$Action = 'Inspect'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
$service = Join-Path $Root $ServiceDir
$state = Join-Path $service 'state'
$schedulerFiles = @('manual_scheduler.py','price_scheduler.py','review_reply_scheduler.py','promotion_scheduler.py')
$stopFiles = @('manual_scheduler.stop','price_scheduler.stop','review_reply_scheduler.stop','promotion_scheduler.stop')
$infrastructure = @('panel.py') + $schedulerFiles

function Project-Processes {
    $prefix = $Root + '\'
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and $_.Name -match '^python(w)?\.exe$' -and
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or
         $_.CommandLine -and $_.CommandLine.IndexOf($prefix,[StringComparison]::OrdinalIgnoreCase) -ge 0) -and
        $_.CommandLine -notmatch '[\\/]updater[\\/]'
    })
}

function Script-Name($Process) {
    foreach ($name in $infrastructure) {
        $expected = Join-Path $service $name
        if ($Process.CommandLine.IndexOf($expected,[StringComparison]::OrdinalIgnoreCase) -ge 0) { return $name }
    }
    return ''
}

function Check-Idle {
    foreach ($task in @(Get-ScheduledTask -ErrorAction Stop)) {
        foreach ($taskAction in $task.Actions) {
            $command = [string]$taskAction.Execute + ' ' + [string]$taskAction.Arguments
            if ($task.State -ne 'Disabled' -and $command.IndexOf($Root,[StringComparison]::OrdinalIgnoreCase) -ge 0) {
                throw 'An enabled Windows scheduled task targets this deployment. Disable it before updating.'
            }
        }
    }
    $processes = Project-Processes
    foreach ($process in $processes) {
        if (-not (Script-Name $process)) { throw 'A collector, login helper or other project process is running. Try again when idle.' }
    }
    foreach ($file in @('status.json','manual_scheduler_status.json','price_scheduler_status.json','review_reply_scheduler_status.json','promotion_scheduler_status.json')) {
        $path = Join-Path $state $file
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $data = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($data.last_run_status -in @('starting','running','stopping') -or
            $data.scheduler_status -in @('starting','running','collecting','stopping')) {
            throw 'Task status is active. Finish tasks (or reconcile stale status) before updating.'
        }
    }
    return $processes
}

function Start-Saved([bool]$PanelOnly) {
    $saved = Get-Content -LiteralPath $Snapshot -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($item in $saved.processes) {
        if (($item.script -eq 'panel.py') -ne $PanelOnly) { continue }
        if (@(Project-Processes | Where-Object { (Script-Name $_) -eq $item.script }).Count) { continue }
        $script = Join-Path $service $item.script
        if (-not $PanelOnly) {
            $index = [array]::IndexOf($schedulerFiles,$item.script)
            if ($index -ge 0) {
                $stop = Join-Path $state $stopFiles[$index]
                if (Test-Path -LiteralPath $stop) { Remove-Item -LiteralPath $stop }
                $pidFile = Join-Path $state ($item.script.Replace('.py','.pid'))
                if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile }
            }
        }
        $process = Start-Process -FilePath $item.executable -ArgumentList ('"' + $script + '"') -WorkingDirectory $service -WindowStyle Hidden -PassThru
        if ($PanelOnly) { Set-Content -LiteralPath (Join-Path $state 'panel.pid') -Value $process.Id -Encoding ASCII }
        Start-Sleep -Milliseconds 600
        if ($process.HasExited) { throw ('Failed to restart ' + $item.script) }
    }
}

switch ($Action) {
    'Inspect' {
        $processes = Check-Idle
        $names = @($processes | ForEach-Object { Script-Name $_ })
        if ($names.Count -ne @($names | Select-Object -Unique).Count) { throw 'Duplicate service processes detected.' }
        $saved = @{processes=@($processes | ForEach-Object { @{script=(Script-Name $_); executable=$_.ExecutablePath; pid=$_.ProcessId} })}
        $saved | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Snapshot -Encoding UTF8
    }
    'Suspend' {
        $processes = Check-Idle
        New-Item -ItemType Directory -Path $state -Force | Out-Null
        foreach ($file in $stopFiles) { Set-Content -LiteralPath (Join-Path $state $file) -Value 'update' -Encoding ASCII }
        foreach ($process in $processes) {
            if ((Script-Name $process) -eq 'panel.py') { Stop-Process -Id $process.ProcessId -ErrorAction Stop }
        }
        $deadline = (Get-Date).AddSeconds(30)
        do {
            if (-not @(Project-Processes).Count) { break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if (@(Project-Processes).Count) { throw 'Project processes did not exit; no files will be updated.' }
    }
    'Panel' { Start-Saved $true }
    'Schedulers' { Start-Saved $false }
    'Undo' {
        foreach ($process in @(Project-Processes)) {
            if ((Script-Name $process) -ne 'panel.py') { throw 'A task is running; refuse rollback over active code.' }
            Stop-Process -Id $process.ProcessId -ErrorAction Stop
        }
    }
}
