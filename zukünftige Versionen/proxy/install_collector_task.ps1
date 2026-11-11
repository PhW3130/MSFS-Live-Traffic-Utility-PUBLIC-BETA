[CmdletBinding()]
param(
    [string] $TaskName = 'LiveTrafficCollector'
)

$ErrorActionPreference = 'Stop'
$watchdog = Join-Path $PSScriptRoot 'run_collector_watchdog.ps1'
$collector = Join-Path $PSScriptRoot 'ltu_collector.exe'
if (-not (Test-Path -LiteralPath $watchdog -PathType Leaf)) {
    throw "Watchdog not found: $watchdog"
}
if (-not (Test-Path -LiteralPath $collector -PathType Leaf)) {
    throw "Collector executable not found: $collector"
}

$powerShell = Join-Path $PSHOME 'powershell.exe'
$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $watchdog
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Autostart task '$TaskName' installed for the current Windows user."
Write-Host 'The collector is supervised, restarted after failures and writes bounded logs under .\logs.'
