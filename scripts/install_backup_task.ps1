param([string]$PythonPath = (Get-Command python -ErrorAction Stop).Source)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backupRunner = Join-Path $PSScriptRoot 'scheduled_backup.py'
$action = New-ScheduledTaskAction -Execute $PythonPath -Argument ('"' + $backupRunner + '"') -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '02:00'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'Aapoorti-E-Franchise-Daily-Backup' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Verified online SQLite backup of Aapoorti E-Franchise; retains existing backups.' -Force | Select-Object TaskName, State
