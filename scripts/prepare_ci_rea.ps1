[CmdletBinding()]
param(
    [string] $ReaDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'rea'),
    [string] $TokenPlaceholder = 'CI-UNCONFIGURED-REPLACE-ME'
)

$ErrorActionPreference = 'Stop'

try {
    $reaPath = (Resolve-Path -LiteralPath $ReaDirectory).Path
    $phpPath = Join-Path $reaPath 'php\index.php'
    $demandModulePath = Join-Path $reaPath 'php\client_demand.php'
    $tracksModulePath = Join-Path $reaPath 'php\aircraft_tracks.php'
    $iniPath = Join-Path $reaPath 'proxy\ltu_collector.ini'
    $credentialsExamplePath = Join-Path $reaPath 'proxy\credentials.example.json'
    $watchdogPath = Join-Path $reaPath 'proxy\run_collector_watchdog.ps1'
    $installTaskPath = Join-Path $reaPath 'proxy\install_collector_task.ps1'
    $removeTaskPath = Join-Path $reaPath 'proxy\remove_collector_task.ps1'
    $readmePath = Join-Path $reaPath 'LIESMICH.txt'
    $thirdPartyNoticesPath = Join-Path $reaPath 'user\MSFS Live Traffic Utility\THIRD_PARTY_NOTICES.txt'
    foreach ($path in @($phpPath, $demandModulePath, $tracksModulePath, $iniPath, $credentialsExamplePath,
                        $watchdogPath, $installTaskPath, $removeTaskPath, $readmePath,
                        $thirdPartyNoticesPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Build-Ausgabe fehlt: $path"
        }
    }
    foreach ($scriptPath in @($watchdogPath, $installTaskPath, $removeTaskPath)) {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $scriptPath, [ref]$tokens, [ref]$parseErrors) | Out-Null
        if ($parseErrors.Count -gt 0) {
            throw ('PowerShell-Syntaxfehler in {0}: {1}' -f $scriptPath, $parseErrors[0].Message)
        }
    }
    $credentialsPath = Join-Path $reaPath 'proxy\credentials.json'
    if (Test-Path -LiteralPath $credentialsPath) {
        throw 'Build-Ausgabe enthaelt credentials.json; OpenSky-Secrets duerfen nie im committeten rea-Ordner liegen.'
    }
    $credentialsTemplate = [System.IO.File]::ReadAllText($credentialsExamplePath)
    $hasClientIdPlaceholder = $credentialsTemplate -match 'PASTE_OPEN_SKY_CLIENT_ID_HERE'
    $hasClientSecretPlaceholder = $credentialsTemplate -match 'PASTE_OPEN_SKY_CLIENT_SECRET_HERE'
    if (-not $hasClientIdPlaceholder -or -not $hasClientSecretPlaceholder) {
        throw 'credentials.example.json ist nicht mehr ein rein unverbindlicher Platzhalter.'
    }

    $phpText = [System.IO.File]::ReadAllText($phpPath)
    $iniText = [System.IO.File]::ReadAllText($iniPath)
    $phpPattern = "define\('LTU_TOKEN'\s*,\s*'([^']*)'\s*\);"
    $iniPattern = '(?im)^([ \t]*token[ \t]*=[ \t]*)([^\r\n]*)'
    $phpMatches = [regex]::Matches($phpText, $phpPattern)
    $iniMatches = [regex]::Matches($iniText, $iniPattern)
    if ($phpMatches.Count -ne 1 -or $iniMatches.Count -ne 1) {
        throw 'PHP-Token oder Collector-INI konnte nicht eindeutig validiert werden.'
    }
    if (($phpMatches[0].Groups[1].Value -ne $TokenPlaceholder) -or
        ($iniMatches[0].Groups[2].Value.Trim() -ne $TokenPlaceholder)) {
        throw 'rea-Build-Ausgabe enthält nicht den erwarteten nicht-geheimen Token-Platzhalter.'
    }

    $toolsPath = Join-Path $reaPath 'tools'
    New-Item -ItemType Directory -Path $toolsPath -Force | Out-Null
    $repoScripts = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts'
    Copy-Item -LiteralPath (Join-Path $repoScripts 'generate_token.ps1') -Destination $toolsPath -Force
    Copy-Item -LiteralPath (Join-Path $repoScripts 'apply_build_token.ps1') -Destination $toolsPath -Force

    $notice = @"
ACHTUNG: Dieser von GitHub Actions erzeugte rea-Ordner wird nach erfolgreichem
Build in das Repository auf main committet und ist noch NICHT deploybereit.
PHP und Collector-INI enthalten absichtlich denselben oeffentlichen CI-Platzhalter:
$TokenPlaceholder
OpenSky-Credentials sind NICHT enthalten. credentials.example.json ist nur eine
Vorlage; echte OAuth2-Zugangsdaten lokal auf dem Proxy-PC in credentials.json setzen.
Diesen Ordner NICHT unveraendert auf einen Webserver kopieren oder den Collector starten.

Privates Token erzeugen und in beide Dateien eintragen (PowerShell im entpackten rea-Ordner):
  .\tools\generate_token.ps1 -OutputPath .\token.txt
  .\tools\apply_build_token.ps1 -TokenFile .\token.txt -PhpFile .\php\index.php -IniFile .\proxy\ltu_collector.ini
Danach token.txt privat aufbewahren/loeschen und erst dann PHP und Collector verteilen.
"@
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText((Join-Path $reaPath 'TOKEN-SETUP.txt'), $notice, $utf8NoBom)

    $readme = [System.IO.File]::ReadAllText($readmePath)
    [System.IO.File]::WriteAllText(
        $readmePath,
        "ACHTUNG: GitHub-Actions-Build mit Token-Platzhalter. Vor Einsatz zuerst TOKEN-SETUP.txt lesen.`r`n`r`n" + $readme,
        $utf8NoBom
    )

    Write-Host "CI-Ausgabe validiert; rea/ kann auf main committet werden: $reaPath"
}
catch {
    $message = $_.Exception.Message
    [Console]::Error.WriteLine($message)
    Write-Output "::error::$message"
    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "Safe-package validation failed: $message"
    }
    exit 1
}
