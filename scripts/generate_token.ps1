[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $OutputPath,

    [switch] $Force
)

$ErrorActionPreference = 'Stop'

try {
    $fullPath = [System.IO.Path]::GetFullPath($OutputPath)
    if ([System.IO.File]::Exists($fullPath) -and -not $Force) {
        throw 'token.txt existiert bereits. Zum Bestaetigen des Ersetzens generate_token.bat verwenden.'
    }

    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = [byte[]]::new(32)
        $rng.GetBytes($bytes)
        $token = [System.BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $rng.Dispose()
    }

    [System.IO.File]::WriteAllText(
        $fullPath,
        $token + [System.Environment]::NewLine,
        [System.Text.Encoding]::ASCII
    )

    Write-Host 'Ein kryptografisch sicheres Zufallstoken wurde in token.txt gespeichert.'
    exit 0
}
catch {
    [System.Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
