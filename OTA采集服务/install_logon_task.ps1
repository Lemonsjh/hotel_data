$ErrorActionPreference = "Stop"
$TaskName = "HotelDataCollectionAtLogon"
$Script = Join-Path $PSScriptRoot "service_control.ps1"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Action Start"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host "已登记当前用户登录时启动，不需要 Windows 密码。任务名：$TaskName"
