param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $Root
$StateDir = Join-Path $Root "state"
$ConfigPath = Join-Path $Root "config\settings.json"
$SchedulerPath = Join-Path $Root "manual_scheduler.py"
$PriceSchedulerPath = Join-Path $Root "price_scheduler.py"
$PanelPath = Join-Path $Root "panel.py"
$SchedulerPidPath = Join-Path $StateDir "manual_scheduler.pid"
$PriceSchedulerPidPath = Join-Path $StateDir "price_scheduler.pid"
$PanelPidPath = Join-Path $StateDir "panel.pid"
$StopPath = Join-Path $StateDir "manual_scheduler.stop"
$PriceStopPath = Join-Path $StateDir "price_scheduler.stop"

function Get-TrackedProcess([string]$PidPath, [string]$ExpectedScript) {
    if (-not (Test-Path -LiteralPath $PidPath)) { return $null }
    $savedPid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$savedPid)) {
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        return $null
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid" -ErrorAction SilentlyContinue
    if (-not $process -or $process.CommandLine -notlike "*$ExpectedScript*") {
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $process
}

function Read-Settings {
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Missing config: $ConfigPath" }
    return Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Resolve-ProjectPath([string]$Value, [string]$DefaultValue) {
    $candidate = if ($Value) { $Value } else { $DefaultValue }
    if ([System.IO.Path]::IsPathRooted($candidate)) { return $candidate }
    return Join-Path $ProjectRoot $candidate
}

function Resolve-PythonPath($Settings) {
    $configured = Resolve-ProjectPath ([string]$Settings.python_path) "runtime\python.exe"
    if (Test-Path -LiteralPath $configured) { return $configured }
    $bundled = Join-Path $ProjectRoot "runtime\python.exe"
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($systemPython) { return $systemPython.Source }
    throw "Python runtime not found. Expected: $bundled"
}

function Start-ManualService {
    $settings = Read-Settings
    $pythonPath = Resolve-PythonPath $settings
    $browserPath = Join-Path $ProjectRoot "runtime\playwright-browsers"
    if (Test-Path -LiteralPath $browserPath) { $env:PLAYWRIGHT_BROWSERS_PATH = $browserPath }
    if (-not (Test-Path -LiteralPath $SchedulerPath)) { throw "Scheduler not found: $SchedulerPath" }
    if (-not (Test-Path -LiteralPath $PriceSchedulerPath)) { throw "Price scheduler not found: $PriceSchedulerPath" }
    if (-not (Test-Path -LiteralPath $PanelPath)) { throw "Panel not found: $PanelPath" }

    $persistentTask = Get-ScheduledTask -TaskName "HotelOTACollector" -ErrorAction SilentlyContinue
    if ($persistentTask -and $persistentTask.State -ne "Disabled") {
        throw "HotelOTACollector is enabled. Disable or uninstall it before using manual scheduling."
    }

    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    Remove-Item -LiteralPath $StopPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PriceStopPath -Force -ErrorAction SilentlyContinue

    $scheduler = Get-TrackedProcess $SchedulerPidPath "manual_scheduler.py"
    if (-not $scheduler) {
        Start-Process -FilePath $pythonPath -ArgumentList "`"$SchedulerPath`"" `
            -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
        Start-Sleep -Seconds 1
        $scheduler = Get-TrackedProcess $SchedulerPidPath "manual_scheduler.py"
        if (-not $scheduler) { throw "Manual scheduler failed to start. Check logs\manual_scheduler.log." }
    }

    $priceSchedulerEnabled = $true
    if ($null -ne $settings.price_scheduler -and $null -ne $settings.price_scheduler.enabled) {
        $priceSchedulerEnabled = [bool]$settings.price_scheduler.enabled
    }
    if ($priceSchedulerEnabled) {
        $priceScheduler = Get-TrackedProcess $PriceSchedulerPidPath "price_scheduler.py"
        if (-not $priceScheduler) {
            Start-Process -FilePath $pythonPath -ArgumentList "`"$PriceSchedulerPath`"" `
                -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
            Start-Sleep -Seconds 1
            $priceScheduler = Get-TrackedProcess $PriceSchedulerPidPath "price_scheduler.py"
            if (-not $priceScheduler) { throw "Price scheduler failed to start. Check logs\price_scheduler.log." }
        }
    }

    $port = [int]($settings.service.panel_port)
    if (-not $port) { $port = 8765 }
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if ($owner.CommandLine -notlike "*panel.py*") { throw "Port $port is occupied by another process." }
        Set-Content -LiteralPath $PanelPidPath -Value $owner.ProcessId -Encoding ASCII
    } else {
        $panel = Get-TrackedProcess $PanelPidPath "panel.py"
        if (-not $panel) {
            $panelProcess = Start-Process -FilePath $pythonPath -ArgumentList "`"$PanelPath`"" `
                -WorkingDirectory $Root -WindowStyle Hidden -PassThru
            Set-Content -LiteralPath $PanelPidPath -Value $panelProcess.Id -Encoding ASCII
        }
        Start-Sleep -Seconds 2
    }

    $hostName = [string]$settings.service.panel_host
    if (-not $hostName) { $hostName = "127.0.0.1" }
    $url = "http://${hostName}:$port"
    Start-Process $url
    Write-Host "Hotel data collection service started: $url"
}

function Stop-ManualService {
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    Set-Content -LiteralPath $StopPath -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss") -Encoding UTF8
    Set-Content -LiteralPath $PriceStopPath -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss") -Encoding UTF8

    $panel = Get-TrackedProcess $PanelPidPath "panel.py"
    if ($panel) { Stop-Process -Id $panel.ProcessId -Force }
    Remove-Item -LiteralPath $PanelPidPath -Force -ErrorAction SilentlyContinue

    $scheduler = Get-TrackedProcess $SchedulerPidPath "manual_scheduler.py"
    if ($scheduler) {
        Write-Host "Stop requested. An active collection will finish before the scheduler exits."
    } else {
        Remove-Item -LiteralPath $StopPath -Force -ErrorAction SilentlyContinue
        Write-Host "Manual scheduler is not running."
    }

    $priceScheduler = Get-TrackedProcess $PriceSchedulerPidPath "price_scheduler.py"
    if ($priceScheduler) {
        Write-Host "Price scheduler stop requested. An active price task will finish first."
    } else {
        Remove-Item -LiteralPath $PriceStopPath -Force -ErrorAction SilentlyContinue
        Write-Host "Price scheduler is not running."
    }
}

function Show-ManualStatus {
    $scheduler = Get-TrackedProcess $SchedulerPidPath "manual_scheduler.py"
    $priceScheduler = Get-TrackedProcess $PriceSchedulerPidPath "price_scheduler.py"
    $panel = Get-TrackedProcess $PanelPidPath "panel.py"
    Write-Host "scheduler:" $(if ($scheduler) { "running (PID $($scheduler.ProcessId))" } else { "stopped" })
    Write-Host "price scheduler:" $(if ($priceScheduler) { "running (PID $($priceScheduler.ProcessId))" } else { "stopped" })
    Write-Host "panel:" $(if ($panel) { "running (PID $($panel.ProcessId))" } else { "stopped" })
}

switch ($Action) {
    "Start" { Start-ManualService }
    "Stop" { Stop-ManualService }
    "Status" { Show-ManualStatus }
}
