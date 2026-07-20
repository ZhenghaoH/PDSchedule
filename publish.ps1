[CmdletBinding()]
param(
    [string]$Message,
    [string]$Remote = "origin",
    [string]$Branch = "p2p-flexpd",
    [switch]$Yes,
    [switch]$DryRun,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$script:GitExecutable = $null

function Write-Step {
    param([string]$Text)
    Write-Host "[publish] $Text" -ForegroundColor Cyan
}

function Stop-Publish {
    param([string]$Text)
    throw $Text
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    # Quote according to the Windows CommandLineToArgvW rules. In particular,
    # backslashes immediately before a quote or the closing quote are doubled.
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * ($backslashes * 2 + 1)))
            [void]$builder.Append('"')
        }
        else {
            [void]$builder.Append(('\' * $backslashes))
            [void]$builder.Append($character)
        }
        $backslashes = 0
    }
    [void]$builder.Append(('\' * ($backslashes * 2)))
    [void]$builder.Append('"')
    return $builder.ToString()
}

function ConvertTo-OutputLines {
    param([AllowEmptyString()][string]$Text)
    if ([string]::IsNullOrEmpty($Text)) {
        return @()
    }
    return @([regex]::Split($Text.TrimEnd("`r", "`n"), '\r?\n'))
}

function Get-SafeRemoteUrl {
    param([string]$Url)
    return ($Url -replace '^(https?://)[^/@]+@', '$1')
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure,
        [switch]$Quiet
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $script:GitExecutable
    $startInfo.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $encoding = [System.Text.UTF8Encoding]::new($false)
    $startInfo.StandardOutputEncoding = $encoding
    $startInfo.StandardErrorEncoding = $encoding

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $output = @(ConvertTo-OutputLines $stdoutTask.Result)
    $errorOutput = @(ConvertTo-OutputLines $stderrTask.Result)
    $process.Dispose()

    if (-not $Quiet) {
        $output | ForEach-Object { Write-Host $_ }
        $errorOutput | ForEach-Object { Write-Host $_ -ForegroundColor DarkYellow }
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $detail = if ($errorOutput.Count -gt 0) { " $($errorOutput -join ' ')" } else { "" }
        Stop-Publish "git $($Arguments -join ' ') failed with exit code $exitCode.$detail"
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = $output
        ErrorOutput = $errorOutput
    }
}

function Get-GitText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $result = Invoke-Git -Arguments $Arguments -Quiet
    return (($result.Output -join "`n").Trim())
}

function Assert-NoGitOperation {
    $markers = @(
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "rebase-merge",
        "rebase-apply"
    )
    foreach ($marker in $markers) {
        $path = Get-GitText -Arguments @("rev-parse", "--git-path", $marker)
        if (Test-Path -LiteralPath $path) {
            Stop-Publish "An unfinished Git operation exists ($marker). Finish or abort it first."
        }
    }
}

function Get-RemoteHead {
    param(
        [string]$RemoteName,
        [string]$BranchName
    )
    $result = Invoke-Git -Arguments @(
        "ls-remote", "--heads", $RemoteName, "refs/heads/$BranchName"
    ) -Quiet
    $line = ($result.Output | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($line)) {
        Stop-Publish "Remote branch does not exist: $RemoteName/$BranchName"
    }
    return (($line -split "\s+")[0]).Trim()
}

try {
    $gitCommand = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCommand) {
        Stop-Publish "Git is not installed or is unavailable in PATH."
    }
    $script:GitExecutable = $gitCommand.Source
    if ($Remote -notmatch '^[A-Za-z0-9._-]+$') {
        Stop-Publish "Invalid remote name: $Remote"
    }
    $branchCheck = Invoke-Git -Arguments @("check-ref-format", "--branch", $Branch) -AllowFailure -Quiet
    if ($branchCheck.ExitCode -ne 0) {
        Stop-Publish "Invalid branch name: $Branch"
    }

    $scriptDirectory = $PSScriptRoot
    Set-Location -LiteralPath $scriptDirectory
    $repositoryRoot = Get-GitText -Arguments @("rev-parse", "--show-toplevel")
    Set-Location -LiteralPath $repositoryRoot

    Assert-NoGitOperation

    $currentBranchResult = Invoke-Git -Arguments @("symbolic-ref", "--quiet", "--short", "HEAD") -AllowFailure -Quiet
    if ($currentBranchResult.ExitCode -ne 0) {
        Stop-Publish "Detached HEAD is not supported. Switch to $Branch first."
    }
    $currentBranch = (($currentBranchResult.Output -join "`n").Trim())
    if ($currentBranch -ne $Branch) {
        Stop-Publish "Current branch is '$currentBranch'; expected '$Branch'."
    }

    $remoteCheck = Invoke-Git -Arguments @("remote", "get-url", $Remote) -AllowFailure -Quiet
    if ($remoteCheck.ExitCode -ne 0) {
        Stop-Publish "Remote does not exist: $Remote"
    }
    $remoteUrl = (($remoteCheck.Output -join "`n").Trim())
    $displayRemoteUrl = Get-SafeRemoteUrl $remoteUrl

    Write-Step "Repository: $repositoryRoot"
    Write-Step "Target: $Remote/$Branch ($displayRemoteUrl)"
    Write-Step "Fetching the latest remote branch..."
    Invoke-Git -Arguments @(
        "fetch", $Remote,
        "refs/heads/${Branch}:refs/remotes/${Remote}/${Branch}"
    ) | Out-Null

    $remoteRef = "refs/remotes/$Remote/$Branch"
    $fetchedRemoteHead = Get-GitText -Arguments @("rev-parse", $remoteRef)
    $difference = Get-GitText -Arguments @(
        "rev-list", "--left-right", "--count", "HEAD...$remoteRef"
    )
    $counts = @($difference -split "\s+")
    if ($counts.Count -ne 2) {
        Stop-Publish "Could not parse local/remote commit difference: $difference"
    }
    $localAhead = [int]$counts[0]
    $remoteAhead = [int]$counts[1]
    Write-Step "Commit difference: local ahead=$localAhead, remote ahead=$remoteAhead"
    if ($remoteAhead -gt 0) {
        Stop-Publish (
            "Remote branch is ahead or histories have diverged. " +
            "Synchronize first; this script will not merge or rebase automatically."
        )
    }

    $statusResult = Invoke-Git -Arguments @(
        "status", "--short", "--untracked-files=all"
    ) -Quiet
    $statusLines = @($statusResult.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $hasWorktreeChanges = $statusLines.Count -gt 0

    if ($hasWorktreeChanges) {
        Write-Host ""
        Write-Host "All non-ignored changes below will be staged with git add -A:" -ForegroundColor Yellow
        $statusLines | ForEach-Object { Write-Host "  $_" }
    }
    else {
        Write-Step "The working tree has no non-ignored changes."
    }

    if ($DryRun) {
        Write-Host ""
        Write-Step "Dry run only; no files will be staged, committed, or pushed."
        if ($hasWorktreeChanges) {
            Write-Host "Would run: git add -A"
            Write-Host "Would run: git diff --cached --check"
            if ([string]::IsNullOrWhiteSpace($Message)) {
                Write-Host "Would request a non-empty commit message."
            }
            else {
                Write-Host "Would commit with message: $($Message.Trim())"
            }
        }
        if ($hasWorktreeChanges -or $localAhead -gt 0) {
            Write-Host "Would verify and push: git push $Remote HEAD:refs/heads/$Branch"
        }
        else {
            Write-Step "Nothing needs to be published."
        }
        exit 0
    }

    if (-not $hasWorktreeChanges -and $localAhead -eq 0) {
        Write-Step "Nothing needs to be committed or pushed."
        exit 0
    }

    if ($hasWorktreeChanges) {
        if ([string]::IsNullOrWhiteSpace($Message)) {
            $Message = Read-Host "Commit message"
        }
        if ([string]::IsNullOrWhiteSpace($Message)) {
            Stop-Publish "Commit message cannot be empty."
        }
        $Message = $Message.Trim()
    }

    if (-not $Yes) {
        $answer = Read-Host "Continue with commit/push to $Remote/$Branch? [y/N]"
        if ($answer -notmatch '^(?i:y|yes)$') {
            Write-Step "Cancelled; no files were staged by this script."
            exit 0
        }
    }

    if ($hasWorktreeChanges) {
        Write-Step "Staging all non-ignored changes..."
        Invoke-Git -Arguments @("add", "-A") | Out-Null

        $checkResult = Invoke-Git -Arguments @("diff", "--cached", "--check") -AllowFailure
        if ($checkResult.ExitCode -ne 0) {
            Stop-Publish "Staged changes failed git diff --cached --check. The index was kept for inspection."
        }

        $cachedResult = Invoke-Git -Arguments @("diff", "--cached", "--quiet") -AllowFailure -Quiet
        if ($cachedResult.ExitCode -gt 1) {
            Stop-Publish "Could not inspect the staged changes."
        }
        if ($cachedResult.ExitCode -eq 1) {
            Write-Host ""
            Write-Host "Staged changes:" -ForegroundColor Yellow
            Invoke-Git -Arguments @("diff", "--cached", "--name-status") | Out-Null
            Write-Step "Creating commit..."
            Invoke-Git -Arguments @("commit", "-m", $Message) | Out-Null
        }
        else {
            Write-Step "No content changes remained after staging; skipping commit."
        }
    }

    $head = Get-GitText -Arguments @("rev-parse", "HEAD")
    $difference = Get-GitText -Arguments @(
        "rev-list", "--left-right", "--count", "HEAD...$remoteRef"
    )
    $counts = @($difference -split "\s+")
    $localAhead = [int]$counts[0]
    if ($localAhead -eq 0) {
        Write-Step "No local commits need to be pushed."
        exit 0
    }

    Write-Step "Checking that the remote branch has not changed..."
    $currentRemoteHead = Get-RemoteHead -RemoteName $Remote -BranchName $Branch
    if ($currentRemoteHead -ne $fetchedRemoteHead) {
        Stop-Publish (
            "Remote branch changed after fetch. Local commit $head was kept, but push was stopped. " +
            "Synchronize and review before retrying."
        )
    }

    Write-Step "Pushing $head to $Remote/$Branch..."
    Invoke-Git -Arguments @(
        "push", $Remote, "HEAD:refs/heads/$Branch"
    ) | Out-Null

    Invoke-Git -Arguments @("fetch", $Remote, "refs/heads/${Branch}:refs/remotes/${Remote}/${Branch}") | Out-Null
    $finalDifference = Get-GitText -Arguments @(
        "rev-list", "--left-right", "--count", "HEAD...$remoteRef"
    )
    Write-Host ""
    Write-Step "Published successfully."
    Write-Host "Commit: $head"
    Write-Host "Target: $Remote/$Branch"
    Write-Host "Remote: $displayRemoteUrl"
    Write-Host "Final local/remote difference: $finalDifference"
    exit 0
}
catch {
    Write-Host "[publish] ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
