[CmdletBinding()]
param(
    [string] $TaskName = 'LiveTrafficCollector'
)

$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Autostart task '$TaskName' does not exist."
    exit 0
}
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$collectorPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'ltu_collector.exe'))
Get-CimInstance Win32_Process -Filter "Name='ltu_collector.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and
        [System.String]::Equals($_.ExecutablePath, $collectorPath,
            [System.StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Autostart task '$TaskName' removed. Its collector process is stopped; existing logs are kept under .\logs."
