[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $TokenFile,

    [string] $PhpFile,
    [string] $IniFile,

    [switch] $ValidateOnly
)

$ErrorActionPreference = 'Stop'

try {
    $tokenPath = (Resolve-Path -LiteralPath $TokenFile).Path
    $lines = [System.IO.File]::ReadAllLines($tokenPath)
    $values = @(
        foreach ($line in $lines) {
            $value = $line.Trim()
            if ($value -and -not $value.StartsWith('#') -and -not $value.StartsWith(';')) {
                $value
            }
        }
    )

    if ($values.Count -ne 1) {
        throw 'token.txt muss genau einen Tokenwert enthalten.'
    }

    $token = $values[0]
    if ($token -notmatch '\A[A-Za-z0-9._~-]{8,256}\z') {
        throw 'Token muss 8-256 Zeichen haben: Buchstaben, Ziffern, Punkt, Unterstrich, Tilde oder Bindestrich.'
    }

    if ($ValidateOnly) {
        Write-Host 'token.txt ist gueltig.'
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($PhpFile) -or [string]::IsNullOrWhiteSpace($IniFile)) {
        throw 'PhpFile und IniFile muessen angegeben werden, ausser bei ValidateOnly.'
    }

    $phpPath = (Resolve-Path -LiteralPath $PhpFile).Path
    $iniPath = (Resolve-Path -LiteralPath $IniFile).Path
    $phpText = [System.IO.File]::ReadAllText($phpPath)
    $iniText = [System.IO.File]::ReadAllText($iniPath)

    $phpPattern = "define\('LTU_TOKEN'\s*,\s*'[^']*'\s*\);"
    if ([regex]::Matches($phpText, $phpPattern).Count -ne 1) {
        throw 'In der Ausgabe-PHP wurde nicht genau eine LTU_TOKEN-Definition gefunden.'
    }

    $iniPattern = '(?im)^([ \t]*token[ \t]*=[ \t]*).*$'
    if ([regex]::Matches($iniText, $iniPattern).Count -ne 1) {
        throw 'In der Collector-INI wurde nicht genau eine token-Einstellung gefunden.'
    }

    $phpText = [regex]::Replace($phpText, $phpPattern, "define('LTU_TOKEN', '$token');")
    $iniText = [regex]::Replace($iniText, $iniPattern, '${1}' + $token)

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($phpPath, $phpText, $utf8NoBom)
    [System.IO.File]::WriteAllText($iniPath, $iniText, $utf8NoBom)

    Write-Host 'Zentrales Token in Ausgabe-PHP und Collector-INI eingetragen.'
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
