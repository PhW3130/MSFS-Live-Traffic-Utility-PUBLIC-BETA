[CmdletBinding()]
param(
    [string] $ReaDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'rea'),
    [ValidateRange(1, 100)] [int] $ChunkSizeMiB = 20,
    [ValidateRange(1, 1000)] [int] $MaxFilesPerCommit = 50,
    [ValidateRange(1, 10)] [int] $MaxPushAttempts = 4
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot
$tempBranchCreated = $false
$publishedToMain = $false
$tempBranch = ''
try {
    $reaPath = (Resolve-Path -LiteralPath $ReaDirectory).Path
    if (-not (Test-Path -LiteralPath $reaPath -PathType Container)) {
        throw "rea build directory does not exist: $reaPath"
    }

    $baseMain = [string](git rev-parse HEAD)
    if ($LASTEXITCODE -ne 0) { throw 'Could not determine the checked-out main commit.' }
    $baseMain = $baseMain.Trim()

    $runId = $env:GITHUB_RUN_ID
    if (-not $runId) { $runId = [guid]::NewGuid().ToString('N') }
    $runAttempt = $env:GITHUB_RUN_ATTEMPT
    if (-not $runAttempt) { $runAttempt = '1' }
    $tempBranch = "ci/rea-publish-$runId-$runAttempt"

    git config user.name 'github-actions[bot]'
    if ($LASTEXITCODE -ne 0) { throw 'Could not set the Git commit author name.' }
    git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
    if ($LASTEXITCODE -ne 0) { throw 'Could not set the Git commit author email.' }

    # Stage once to identify the full delta, then divide it into small packs for reliable HTTPS pushes.
    git add --force --all -- rea
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage the generated rea folder.' }
    $changedPaths = @(git -c core.quotepath=false diff --cached --name-only -- rea)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect staged rea changes.' }
    if ($changedPaths.Count -eq 0) {
        Write-Host 'The generated rea folder is unchanged.'
        return
    }

    $chunkLimitBytes = [long]$ChunkSizeMiB * 1MB
    $maxFileBytes = 100MB
    $chunks = [System.Collections.Generic.List[object]]::new()
    $currentPaths = [System.Collections.Generic.List[string]]::new()
    [long]$currentBytes = 0

    foreach ($path in $changedPaths) {
        $filePath = Join-Path $repoRoot ($path.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        [long]$fileBytes = 0
        if (Test-Path -LiteralPath $filePath -PathType Leaf) {
            $fileBytes = [long](Get-Item -LiteralPath $filePath).Length
        }
        if ($fileBytes -ge $maxFileBytes) {
            throw "Generated file exceeds GitHub's 100 MiB per-file Git limit: $path ($fileBytes bytes)."
        }

        if ($currentPaths.Count -gt 0 -and
            (($currentBytes + $fileBytes) -gt $chunkLimitBytes -or
             $currentPaths.Count -ge $MaxFilesPerCommit)) {
            $chunks.Add([pscustomobject]@{
                Paths = $currentPaths.ToArray()
                Bytes = $currentBytes
            })
            $currentPaths = [System.Collections.Generic.List[string]]::new()
            $currentBytes = 0
        }
        $currentPaths.Add($path)
        $currentBytes += $fileBytes
    }
    if ($currentPaths.Count -gt 0) {
        $chunks.Add([pscustomobject]@{
            Paths = $currentPaths.ToArray()
            Bytes = $currentBytes
        })
    }

    git reset --quiet -- rea
    if ($LASTEXITCODE -ne 0) { throw 'Could not clear the temporary staging area.' }

    Write-Host ("Publishing {0} changed files in {1} size-limited commits to a temporary branch." -f
        $changedPaths.Count, $chunks.Count)
    for ($chunkIndex = 0; $chunkIndex -lt $chunks.Count; $chunkIndex++) {
        $chunk = $chunks[$chunkIndex]
        $gitArgs = @('add', '--force', '--all', '--') + $chunk.Paths
        & git @gitArgs
        if ($LASTEXITCODE -ne 0) {
            throw ("Could not stage rea publish part {0}/{1}." -f ($chunkIndex + 1), $chunks.Count)
        }

        $stagedPaths = @(git diff --cached --name-only -- rea)
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the current rea publish part.' }
        if ($stagedPaths.Count -eq 0) { continue }

        $partNumber = $chunkIndex + 1
        Write-Host ("Pushing part {0}/{1}: {2} files, approximately {3:N1} MiB." -f
            $partNumber, $chunks.Count, $stagedPaths.Count, ($chunk.Bytes / 1MB))
        $commitMessage = "Update generated rea package ($partNumber/$($chunks.Count)) [skip ci]"
        git commit -m $commitMessage
        if ($LASTEXITCODE -ne 0) {
            throw ("Could not commit rea publish part {0}/{1}." -f $partNumber, $chunks.Count)
        }

        $pushSucceeded = $false
        $lastPushExitCode = 0
        for ($attempt = 1; $attempt -le $MaxPushAttempts; $attempt++) {
            git -c http.postBuffer=134217728 -c http.version=HTTP/1.1 push origin "HEAD:refs/heads/$tempBranch"
            $lastPushExitCode = $LASTEXITCODE
            if ($lastPushExitCode -eq 0) {
                $pushSucceeded = $true
                $tempBranchCreated = $true
                break
            }

            # A timeout may happen after GitHub accepted the ref update but before the runner got the response.
            git fetch --no-tags origin "refs/heads/${tempBranch}:refs/remotes/origin/${tempBranch}"
            if ($LASTEXITCODE -eq 0) {
                $remoteHead = [string](git rev-parse "refs/remotes/origin/$tempBranch")
                $remoteHeadExitCode = $LASTEXITCODE
                $localHead = [string](git rev-parse HEAD)
                $localHeadExitCode = $LASTEXITCODE
                if ($remoteHeadExitCode -eq 0 -and $localHeadExitCode -eq 0 -and
                    $remoteHead.Trim() -eq $localHead.Trim()) {
                    Write-Host 'The temporary branch already contains this rea part; treating the push as successful.'
                    $pushSucceeded = $true
                    $tempBranchCreated = $true
                    break
                }
            }

            if ($attempt -lt $MaxPushAttempts) {
                $delaySeconds = [int](5 * [math]::Pow(2, ($attempt - 1)))
                Write-Warning ("Temporary push part {0}/{1} failed with exit code {2}; retrying in {3} seconds ({4}/{5})." -f
                    $partNumber, $chunks.Count, $lastPushExitCode, $delaySeconds, $attempt, $MaxPushAttempts)
                Start-Sleep -Seconds $delaySeconds
            }
        }
        if (-not $pushSucceeded) {
            throw ("Could not upload rea publish part {0}/{1} after {2} attempts (last exit code {3})." -f
                $partNumber, $chunks.Count, $MaxPushAttempts, $lastPushExitCode)
        }
    }

    git fetch --no-tags origin main
    if ($LASTEXITCODE -ne 0) { throw 'Could not fetch main before publishing the completed rea package.' }
    $remoteMain = [string](git rev-parse refs/remotes/origin/main)
    if ($LASTEXITCODE -ne 0) { throw 'Could not determine the current remote main commit.' }
    if ($remoteMain.Trim() -ne $baseMain) {
        throw 'main changed during the package build; the rea package was not published. The next main build can rebuild against the new commit.'
    }

    $mainPushSucceeded = $false
    $lastMainPushExitCode = 0
    for ($attempt = 1; $attempt -le $MaxPushAttempts; $attempt++) {
        git -c http.postBuffer=134217728 -c http.version=HTTP/1.1 push origin HEAD:main
        $lastMainPushExitCode = $LASTEXITCODE
        if ($lastMainPushExitCode -eq 0) {
            $mainPushSucceeded = $true
            break
        }

        git fetch --no-tags origin main
        if ($LASTEXITCODE -eq 0) {
            $remoteMain = [string](git rev-parse refs/remotes/origin/main)
            $remoteMainExitCode = $LASTEXITCODE
            $localHead = [string](git rev-parse HEAD)
            $localHeadExitCode = $LASTEXITCODE
            if ($remoteMainExitCode -eq 0 -and $localHeadExitCode -eq 0 -and
                $remoteMain.Trim() -eq $localHead.Trim()) {
                Write-Host 'Remote main already contains the completed rea package; treating the push as successful.'
                $mainPushSucceeded = $true
                break
            }
        }

        if ($attempt -lt $MaxPushAttempts) {
            $delaySeconds = [int](5 * [math]::Pow(2, ($attempt - 1)))
            Write-Warning ("Final main update failed with exit code {0}; retrying in {1} seconds ({2}/{3})." -f
                $lastMainPushExitCode, $delaySeconds, $attempt, $MaxPushAttempts)
            Start-Sleep -Seconds $delaySeconds
        }
    }
    if (-not $mainPushSucceeded) {
        throw ("Could not publish the completed rea folder to main after {0} attempts (last exit code {1})." -f
            $MaxPushAttempts, $lastMainPushExitCode)
    }
    $publishedToMain = $true

    git push origin --delete $tempBranch
    if ($LASTEXITCODE -eq 0) {
        $tempBranchCreated = $false
    }
    else {
        Write-Warning "Package is published, but temporary branch '$tempBranch' could not be deleted."
    }

    if ($env:GITHUB_STEP_SUMMARY) {
        "Committed the generated rea folder in $($chunks.Count) size-limited parts and then updated main." |
            Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
}
catch {
    if ($tempBranchCreated -and -not $publishedToMain) {
        git push origin --delete $tempBranch
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not clean up temporary upload branch '$tempBranch'."
        }
    }
    throw
}
finally {
    Pop-Location
}
