[CmdletBinding()]
param(
    [string[]] $CollectorArguments = @(),
    [int] $RestartDelaySeconds = 15,
    [int] $MaximumRunHours = 24,
    [int] $LogRetentionDays = 14
)

$ErrorActionPreference = 'Stop'
$collectorPath = Join-Path $PSScriptRoot 'ltu_collector.exe'
$logDirectory = Join-Path $PSScriptRoot 'logs'
$watchdogLog = Join-Path $logDirectory 'watchdog.log'

if (-not (Test-Path -LiteralPath $collectorPath -PathType Leaf)) {
    throw "Collector executable not found: $collectorPath"
}
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Write-WatchdogLog([string] $Message) {
    if ((Test-Path -LiteralPath $watchdogLog -PathType Leaf) -and
        (Get-Item -LiteralPath $watchdogLog).Length -ge 1048576) {
        Move-Item -LiteralPath $watchdogLog -Destination ($watchdogLog + '.1') -Force
    }
    $line = '{0:u} {1}' -f (Get-Date), $Message
    Add-Content -LiteralPath $watchdogLog -Value $line -Encoding UTF8
}

$failureCount = 0
while ($true) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdoutLog = Join-Path $logDirectory "collector-$stamp.stdout.log"
    $stderrLog = Join-Path $logDirectory "collector-$stamp.stderr.log"
    $started = Get-Date
    $process = $null
    $exitCode = $null

    try {
        Write-WatchdogLog "Starting collector; output: $stamp"
        $startParams = @{
            FilePath = $collectorPath
            WorkingDirectory = $PSScriptRoot
            PassThru = $true
            WindowStyle = 'Hidden'
            RedirectStandardOutput = $stdoutLog
            RedirectStandardError = $stderrLog
        }
        if ($CollectorArguments.Count -gt 0) {
            $startParams.ArgumentList = $CollectorArguments
        }
        $process = Start-Process @startParams

        $deadline = $started.AddHours([Math]::Max(1, $MaximumRunHours))
        while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
            $process.Refresh()
        }

        if (-not $process.HasExited) {
            Write-WatchdogLog "Scheduled daily restart after $MaximumRunHours hours."
            try { $process.CloseMainWindow() | Out-Null } catch { }
            if (-not $process.WaitForExit(10000)) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                $process.WaitForExit()
            }
        }
        $process.Refresh()
        $exitCode = $process.ExitCode
        Write-WatchdogLog "Collector exited with code $exitCode."
        if ($exitCode -eq 0 -and (Test-Path -LiteralPath $stdoutLog -PathType Leaf) -and
            (Get-Content -LiteralPath $stdoutLog -Raw -ErrorAction SilentlyContinue) -match 'laeuft bereits') {
            Write-WatchdogLog 'A manually started collector is already running; stopping this watchdog instance.'
            exit 0
        }
    }
    catch {
        Write-WatchdogLog "Collector launch/watch failed: $($_.Exception.Message)"
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }

    $runSeconds = ((Get-Date) - $started).TotalSeconds
    if ($runSeconds -ge 600 -or $exitCode -eq 0) {
        $failureCount = 0
    } else {
        $failureCount++
    }
    $backoffMultiplier = [Math]::Pow(2, [Math]::Min(5, $failureCount))
    $restartDelay = [Math]::Min(300, [Math]::Max(1, $RestartDelaySeconds) * $backoffMultiplier)
    Write-WatchdogLog "Restarting in $restartDelay seconds (failure streak $failureCount)."

    # Keep at most 100 recent run logs and discard older files.
    $collectorLogs = @(Get-ChildItem -LiteralPath $logDirectory -File -Filter 'collector-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    $removeLogs = @($collectorLogs | Where-Object {
        $_.LastWriteTime -lt (Get-Date).AddDays(-[Math]::Max(1, $LogRetentionDays))
    })
    if ($collectorLogs.Count -gt 100) {
        $removeLogs += @($collectorLogs | Select-Object -Skip 100)
    }
    $removeLogs | Sort-Object FullName -Unique | Remove-Item -Force -ErrorAction SilentlyContinue

    Start-Sleep -Seconds ([int]$restartDelay)
}
