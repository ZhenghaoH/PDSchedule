[CmdletBinding()]
param(
    [string]$Remote = "origin",
    [string]$BaseBranch = "p2p-flexpd",
    [string]$Branch = "PDSchedule",
    [switch]$Yes,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$script:GitExecutable = $null

function Write-Step {
    param([string]$Text)
    Write-Host "[create-pdschedule] $Text" -ForegroundColor Cyan
}

function Stop-Create {
    param([string]$Text)
    throw $Text
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

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
    $startInfo.WorkingDirectory = (Get-Location).Path
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
        Stop-Create "git $($Arguments -join ' ') failed with exit code $exitCode.$detail"
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

function Get-RemoteHead {
    param(
        [string]$RemoteName,
        [string]$BranchName
    )
    $result = Invoke-Git -Arguments @(
        "ls-remote", "--heads", $RemoteName, "refs/heads/$BranchName"
    ) -Quiet
    $line = $result.Output | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($line)) {
        return $null
    }
    return (($line -split "\s+")[0]).Trim()
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
            Stop-Create "An unfinished Git operation exists ($marker). Finish or abort it first."
        }
    }
}

function Assert-EmptyIndex {
    $result = Invoke-Git -Arguments @("diff", "--cached", "--quiet") -AllowFailure -Quiet
    if ($result.ExitCode -gt 1) {
        Stop-Create "Could not inspect the Git index."
    }
    if ($result.ExitCode -eq 1) {
        Stop-Create (
            "The Git index must be empty before the first push. " +
            "Unstage existing changes; this script will not modify the index."
        )
    }
}

function Assert-ValidName {
    param(
        [string]$Value,
        [string]$Description
    )
    $result = Invoke-Git -Arguments @("check-ref-format", "--branch", $Value) -AllowFailure -Quiet
    if ($result.ExitCode -ne 0) {
        Stop-Create "Invalid $Description name: $Value"
    }
}

try {
    $gitCommand = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCommand) {
        Stop-Create "Git is not installed or is unavailable in PATH."
    }
    $script:GitExecutable = $gitCommand.Source

    if ($Remote -notmatch '^[A-Za-z0-9._-]+$') {
        Stop-Create "Invalid remote name: $Remote"
    }
    Assert-ValidName -Value $BaseBranch -Description "base branch"
    Assert-ValidName -Value $Branch -Description "target branch"
    if ($Branch -ne "PDSchedule") {
        Stop-Create "This project requires the exact target branch name 'PDSchedule'."
    }

    Set-Location -LiteralPath $PSScriptRoot
    $repositoryRoot = Get-GitText -Arguments @("rev-parse", "--show-toplevel")
    Set-Location -LiteralPath $repositoryRoot

    Assert-NoGitOperation
    Assert-EmptyIndex

    $remoteCheck = Invoke-Git -Arguments @("remote", "get-url", $Remote) -AllowFailure -Quiet
    if ($remoteCheck.ExitCode -ne 0) {
        Stop-Create "Remote does not exist: $Remote"
    }

    $status = Invoke-Git -Arguments @(
        "-c", "core.quotepath=false",
        "status", "--porcelain=v1", "--untracked-files=all"
    ) -Quiet
    $statusLines = @($status.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $statusSnapshot = ($statusLines -join "`n")

    $localTarget = Invoke-Git -Arguments @(
        "show-ref", "--verify", "--quiet", "refs/heads/$Branch"
    ) -AllowFailure -Quiet
    if ($localTarget.ExitCode -gt 1) {
        Stop-Create "Could not inspect local branch refs."
    }
    $localBranchExists = $localTarget.ExitCode -eq 0

    if ($localBranchExists) {
        $currentBranchResult = Invoke-Git -Arguments @(
            "symbolic-ref", "--quiet", "--short", "HEAD"
        ) -AllowFailure -Quiet
        if ($currentBranchResult.ExitCode -ne 0) {
            Stop-Create "Detached HEAD is not supported."
        }
        $currentBranch = (($currentBranchResult.Output -join "`n").Trim())
        if ($currentBranch -ne $Branch) {
            Stop-Create (
                "Local branch $Branch already exists, but the current branch is '$currentBranch'. " +
                "Switch to $Branch before first-pushing it."
            )
        }
        $operationMode = "first-push-existing"
        if ($statusLines.Count -gt 0) {
            Write-Host "Working tree changes that will remain local and uncommitted:" -ForegroundColor Yellow
            $statusLines | ForEach-Object { Write-Host "  $_" }
            Write-Step "Only HEAD will be pushed; none of the changes above will be uploaded."
        }
    }
    else {
        $operationMode = "create-and-first-push"
        if ($statusLines.Count -gt 0) {
            Write-Host "Working tree changes:" -ForegroundColor Yellow
            $statusLines | ForEach-Object { Write-Host "  $_" }
            Stop-Create (
                "The working tree must be completely clean before creating a new local $Branch. " +
                "This script does not stash, stage, commit, or delete files."
            )
        }
    }

    Write-Step "Checking remote branches without changing local refs..."
    $baseRemoteHead = Get-RemoteHead -RemoteName $Remote -BranchName $BaseBranch
    if ([string]::IsNullOrWhiteSpace($baseRemoteHead)) {
        Stop-Create "Remote base branch does not exist: $Remote/$BaseBranch"
    }
    $targetRemoteHead = Get-RemoteHead -RemoteName $Remote -BranchName $Branch
    if (-not [string]::IsNullOrWhiteSpace($targetRemoteHead)) {
        Stop-Create "Remote target branch already exists: $Remote/$Branch"
    }

    $headBefore = Get-GitText -Arguments @("rev-parse", "HEAD")
    if ($localBranchExists -and $headBefore -ne $baseRemoteHead) {
        Stop-Create (
            "Local $Branch HEAD ($headBefore) does not exactly match the latest " +
            "$Remote/$BaseBranch ($baseRemoteHead). No branch was pushed."
        )
    }

    Write-Step "Repository: $repositoryRoot"
    Write-Step "Base: $Remote/$BaseBranch ($baseRemoteHead)"
    if ($localBranchExists) {
        Write-Step "Mode: first-push existing local $Branch"
        Write-Step "Local HEAD matches the latest remote base."
    }
    else {
        Write-Step "Mode: create local $Branch and first-push it"
    }
    Write-Step "First push target: $Remote/$Branch"

    if ($DryRun) {
        Write-Step "Dry run only; no branch will be created or pushed."
        Write-Host "Would fetch: $Remote/$BaseBranch"
        if ($localBranchExists) {
            Write-Host "Would keep the existing local branch and push only HEAD: $headBefore"
            Write-Host "Would preserve all working tree and untracked changes."
        }
        else {
            Write-Host "Would create: $Branch from $baseRemoteHead"
        }
        Write-Host "Would push with upstream: $Remote/$Branch"
        exit 0
    }

    if (-not $Yes) {
        if ($localBranchExists) {
            $prompt = (
                "First-push existing $Branch HEAD only, preserving all uncommitted changes? [y/N]"
            )
        }
        else {
            $prompt = "Create and first-push $Branch from $Remote/$BaseBranch? [y/N]"
        }
        $answer = Read-Host $prompt
        if ($answer -notmatch '^(?i:y|yes)$') {
            Write-Step "Cancelled; no branch was created or pushed."
            exit 0
        }
    }

    Write-Step "Fetching the latest base branch..."
    Invoke-Git -Arguments @(
        "fetch", $Remote,
        "refs/heads/${BaseBranch}:refs/remotes/${Remote}/${BaseBranch}"
    ) | Out-Null
    $fetchedBaseHead = Get-GitText -Arguments @(
        "rev-parse", "refs/remotes/$Remote/$BaseBranch"
    )
    if ($fetchedBaseHead -ne $baseRemoteHead) {
        Stop-Create (
            "$Remote/$BaseBranch changed during this run. " +
            "No target branch was pushed; rerun the script to review the new base."
        )
    }
    if ($localBranchExists) {
        $currentHead = Get-GitText -Arguments @("rev-parse", "HEAD")
        if ($currentHead -ne $fetchedBaseHead) {
            Stop-Create (
                "Local $Branch HEAD no longer matches the fetched base. " +
                "No target branch was pushed."
            )
        }
    }

    $targetRemoteHead = Get-RemoteHead -RemoteName $Remote -BranchName $Branch
    if (-not [string]::IsNullOrWhiteSpace($targetRemoteHead)) {
        Stop-Create (
            "Remote target branch was created during this run: $Remote/$Branch. " +
            "No local branch was created."
        )
    }

    if (-not $localBranchExists) {
        Write-Step "Creating local $Branch from $fetchedBaseHead..."
        Invoke-Git -Arguments @(
            "switch", "-c", $Branch, "refs/remotes/$Remote/$BaseBranch"
        ) | Out-Null
    }

    Write-Step "First-pushing $Branch and establishing upstream..."
    Invoke-Git -Arguments @(
        "push", "-u", $Remote, "HEAD:refs/heads/$Branch"
    ) | Out-Null

    Invoke-Git -Arguments @(
        "fetch", $Remote,
        "refs/heads/${Branch}:refs/remotes/${Remote}/${Branch}"
    ) | Out-Null
    $head = Get-GitText -Arguments @("rev-parse", "HEAD")
    $remoteHead = Get-GitText -Arguments @("rev-parse", "refs/remotes/$Remote/$Branch")
    $upstream = Get-GitText -Arguments @(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    if ($head -ne $remoteHead) {
        Stop-Create "Verification failed: local HEAD does not match $Remote/$Branch."
    }
    if ($upstream -ne "$Remote/$Branch") {
        Stop-Create "Verification failed: upstream is '$upstream', expected '$Remote/$Branch'."
    }
    $expectedHeadAfterPush = if ($localBranchExists) { $headBefore } else { $fetchedBaseHead }
    if ($head -ne $expectedHeadAfterPush) {
        Stop-Create "Verification failed: local HEAD is not the expected base commit."
    }

    $statusAfter = Invoke-Git -Arguments @(
        "-c", "core.quotepath=false",
        "status", "--porcelain=v1", "--untracked-files=all"
    ) -Quiet
    $statusAfterLines = @(
        $statusAfter.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $statusAfterSnapshot = ($statusAfterLines -join "`n")
    if ($statusAfterSnapshot -ne $statusSnapshot) {
        Stop-Create (
            "Verification failed: the working tree status changed during the first push. " +
            "Inspect git status before continuing."
        )
    }

    Write-Host ""
    if ($operationMode -eq "first-push-existing") {
        Write-Step "Existing PDSchedule branch first-pushed successfully."
        Write-Step "All pre-existing working tree changes remain local and uncommitted."
    }
    else {
        Write-Step "PDSchedule branch created and first-pushed successfully."
    }
    Write-Host "Commit: $head"
    Write-Host "Upstream: $upstream"
    exit 0
}
catch {
    Write-Host "[create-pdschedule] ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
