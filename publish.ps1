[CmdletBinding()]
param(
    [string]$Message,
    [string]$Remote = "origin",
    [string]$Branch = "PDSchedule",
    [string[]]$Paths = @(),
    [switch]$Yes,
    [switch]$DryRun
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

function Get-GitLines {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $result = Invoke-Git -Arguments $Arguments -Quiet
    return @($result.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
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
            Stop-Publish "An unfinished Git operation exists ($marker). Finish or abort it first."
        }
    }
}

function Assert-EmptyIndex {
    $result = Invoke-Git -Arguments @("diff", "--cached", "--quiet") -AllowFailure -Quiet
    if ($result.ExitCode -gt 1) {
        Stop-Publish "Could not inspect the Git index."
    }
    if ($result.ExitCode -eq 1) {
        Stop-Publish "The Git index is not empty. Unstage existing changes before publishing."
    }
}

function Get-TrackedChanges {
    return @(Get-GitLines -Arguments @(
        "-c", "core.quotepath=false",
        "diff", "--name-only", "--diff-filter=ACDMRTUXB"
    ))
}

function Get-UntrackedFiles {
    return @(Get-GitLines -Arguments @(
        "-c", "core.quotepath=false",
        "ls-files", "--others", "--exclude-standard"
    ))
}

function Get-StagedPaths {
    return @(Get-GitLines -Arguments @(
        "-c", "core.quotepath=false",
        "diff", "--cached", "--name-only", "--diff-filter=ACDMRTUXB"
    ))
}

function Resolve-ExplicitPath {
    param(
        [string]$Value,
        [string]$RepositoryRoot
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        Stop-Publish "An empty value was provided in -Paths."
    }
    $candidate = $Value.Trim().Replace('\', '/')
    while ($candidate.StartsWith("./", [System.StringComparison]::Ordinal)) {
        $candidate = $candidate.Substring(2)
    }
    if (
        [System.IO.Path]::IsPathRooted($candidate) -or
        $candidate.StartsWith(":", [System.StringComparison]::Ordinal) -or
        $candidate -match '[*?\[]'
    ) {
        Stop-Publish (
            "-Paths accepts exact repository-relative file paths only; " +
            "absolute paths, Git pathspec magic, and wildcards are rejected: $Value"
        )
    }
    $segments = @($candidate -split '/')
    if (
        $candidate.Length -eq 0 -or
        $candidate -eq "." -or
        $segments -contains ".." -or
        $segments -contains ".git"
    ) {
        Stop-Publish "Unsafe repository path: $Value"
    }

    $platformPath = $candidate.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $platformPath))
    $rootWithSeparator = $RepositoryRoot.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.StartsWith(
        $rootWithSeparator,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        Stop-Publish "Path escapes the repository: $Value"
    }
    if (Test-Path -LiteralPath $fullPath -PathType Container) {
        Stop-Publish "-Paths accepts files, not directories: $Value"
    }

    $relativePath = $fullPath.Substring($rootWithSeparator.Length).Replace('\', '/')
    $trackedCheck = Invoke-Git -Arguments @(
        "ls-files", "--error-unmatch", "--", $relativePath
    ) -AllowFailure -Quiet
    $fileExists = Test-Path -LiteralPath $fullPath -PathType Leaf
    if (-not $fileExists -and $trackedCheck.ExitCode -ne 0) {
        Stop-Publish "Selected file does not exist and is not tracked: $relativePath"
    }
    return $relativePath
}

function Resolve-ExplicitPaths {
    param(
        [string[]]$Values,
        [string]$RepositoryRoot
    )
    $resolved = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($value in $Values) {
        $path = Resolve-ExplicitPath -Value $value -RepositoryRoot $RepositoryRoot
        if ($seen.Add($path)) {
            [void]$resolved.Add($path)
        }
    }
    return @($resolved)
}

function Assert-StagedPaths {
    param([string[]]$ExpectedPaths)
    $stagedPaths = @(Get-StagedPaths)
    if ($stagedPaths.Count -eq 0) {
        Stop-Publish "No changes were staged."
    }
    if ($stagedPaths.Count -ne $ExpectedPaths.Count) {
        Stop-Publish (
            "The staged path count does not match the reviewed change set. " +
            "Expected $($ExpectedPaths.Count), found $($stagedPaths.Count)."
        )
    }
    foreach ($path in $stagedPaths) {
        if ($ExpectedPaths -notcontains $path) {
            Stop-Publish "An unreviewed path entered the Git index: $path"
        }
    }
    foreach ($path in $ExpectedPaths) {
        if ($stagedPaths -notcontains $path) {
            Stop-Publish "A reviewed path was not staged: $path"
        }
    }
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
    if ($Branch -ne "PDSchedule") {
        Stop-Publish "This project requires the exact branch name 'PDSchedule'."
    }

    Set-Location -LiteralPath $PSScriptRoot
    $repositoryRoot = Get-GitText -Arguments @("rev-parse", "--show-toplevel")
    $repositoryRoot = [System.IO.Path]::GetFullPath($repositoryRoot)
    Set-Location -LiteralPath $repositoryRoot

    Assert-NoGitOperation
    Assert-EmptyIndex

    $currentBranchResult = Invoke-Git -Arguments @(
        "symbolic-ref", "--quiet", "--short", "HEAD"
    ) -AllowFailure -Quiet
    if ($currentBranchResult.ExitCode -ne 0) {
        Stop-Publish "Detached HEAD is not supported."
    }
    $currentBranch = (($currentBranchResult.Output -join "`n").Trim())
    if ($currentBranch -ne $Branch) {
        Stop-Publish "Current branch is '$currentBranch'; expected '$Branch'."
    }

    $remoteCheck = Invoke-Git -Arguments @("remote", "get-url", $Remote) -AllowFailure -Quiet
    if ($remoteCheck.ExitCode -ne 0) {
        Stop-Publish "Remote does not exist: $Remote"
    }

    $upstreamResult = Invoke-Git -Arguments @(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    ) -AllowFailure -Quiet
    if ($upstreamResult.ExitCode -ne 0) {
        Stop-Publish (
            "Branch $Branch has no upstream. Run create_pdschedule.ps1 first."
        )
    }
    $upstream = (($upstreamResult.Output -join "`n").Trim())
    if ($upstream -ne "$Remote/$Branch") {
        Stop-Publish "Upstream is '$upstream'; expected '$Remote/$Branch'."
    }

    $initialRemoteHead = Get-RemoteHead -RemoteName $Remote -BranchName $Branch
    if ([string]::IsNullOrWhiteSpace($initialRemoteHead)) {
        Stop-Publish "Remote branch does not exist: $Remote/$Branch"
    }

    if (-not $DryRun) {
        Write-Step "Fetching the latest $Remote/$Branch..."
        Invoke-Git -Arguments @(
            "fetch", $Remote,
            "refs/heads/${Branch}:refs/remotes/${Remote}/${Branch}"
        ) | Out-Null
    }

    $remoteObject = Invoke-Git -Arguments @(
        "cat-file", "-e", "$initialRemoteHead`^{commit}"
    ) -AllowFailure -Quiet
    if ($remoteObject.ExitCode -ne 0) {
        Stop-Publish (
            "Remote commit $initialRemoteHead is not available locally. " +
            "Run without -DryRun to fetch it."
        )
    }

    $remoteIsAncestor = Invoke-Git -Arguments @(
        "merge-base", "--is-ancestor", $initialRemoteHead, "HEAD"
    ) -AllowFailure -Quiet
    if ($remoteIsAncestor.ExitCode -gt 1) {
        Stop-Publish "Could not compare local and remote histories."
    }
    if ($remoteIsAncestor.ExitCode -eq 1) {
        Stop-Publish (
            "Remote branch is ahead or histories diverged. " +
            "This script will not merge, rebase, reset, or force-push."
        )
    }

    $localAhead = [int](Get-GitText -Arguments @(
        "rev-list", "--count", "$initialRemoteHead..HEAD"
    ))
    $trackedChanges = @(Get-TrackedChanges)
    $untrackedBefore = @(Get-UntrackedFiles)
    $allChanges = @(($trackedChanges + $untrackedBefore) | Sort-Object -Unique)

    $explicitPaths = @()
    $useExplicitPaths = $Paths.Count -gt 0
    if ($useExplicitPaths) {
        $explicitPaths = @(Resolve-ExplicitPaths -Values $Paths -RepositoryRoot $repositoryRoot)
    }

    if ($useExplicitPaths) {
        $selectedStatus = @(Get-GitLines -Arguments (
            @("-c", "core.quotepath=false", "status", "--short", "--") +
            $explicitPaths
        ))
        $selectedPaths = @(
            $allChanges | Where-Object { $explicitPaths -contains $_ }
        )
    }
    else {
        $selectedStatus = @($allChanges)
        $selectedPaths = @($allChanges)
    }
    $hasCommitChanges = $selectedPaths.Count -gt 0
    $selectedUntracked = @(
        $untrackedBefore | Where-Object { $selectedPaths -contains $_ }
    )
    $excludedUntracked = @(
        $untrackedBefore | Where-Object { $selectedPaths -notcontains $_ }
    )

    Write-Step "Repository: $repositoryRoot"
    Write-Step "Target: $Remote/$Branch"
    Write-Step "Remote HEAD: $initialRemoteHead"
    Write-Step "Local commits ahead: $localAhead"

    if ($useExplicitPaths) {
        Write-Host ""
        Write-Host "Explicitly selected paths:" -ForegroundColor Yellow
        $explicitPaths | ForEach-Object { Write-Host "  $_" }
        if ($selectedStatus.Count -gt 0) {
            Write-Host "Selected changes:" -ForegroundColor Yellow
            $selectedStatus | ForEach-Object { Write-Host "  $_" }
        }
        $excludedTracked = @(
            $trackedChanges | Where-Object { $explicitPaths -notcontains $_ }
        )
        if ($excludedTracked.Count -gt 0) {
            Write-Host "Tracked changes not selected:" -ForegroundColor DarkYellow
            $excludedTracked | ForEach-Object { Write-Host "  $_" }
        }
    }
    else {
        if ($trackedChanges.Count -gt 0) {
            Write-Host ""
            Write-Host "Tracked changes to commit:" -ForegroundColor Yellow
            $trackedChanges | ForEach-Object { Write-Host "  $_" }
        }
    }

    if ($selectedUntracked.Count -gt 0) {
        Write-Host ""
        Write-Host "New files to commit:" -ForegroundColor Yellow
        $selectedUntracked | ForEach-Object { Write-Host "  $_" }
    }
    if ($excludedUntracked.Count -gt 0) {
        Write-Host ""
        Write-Host "Untracked files not selected:" -ForegroundColor DarkYellow
        $excludedUntracked | ForEach-Object { Write-Host "  $_" }
    }

    if (-not $hasCommitChanges -and $localAhead -eq 0) {
        if ($useExplicitPaths -and $allChanges.Count -gt 0) {
            Write-Step "No selected changes or local commits need synchronization."
            Write-Step "Changes outside -Paths remain local."
        }
        else {
            Write-Step "Nothing needs to be committed or pushed."
        }
        exit 0
    }

    if ($hasCommitChanges -and [string]::IsNullOrWhiteSpace($Message)) {
        if ($DryRun) {
            Stop-Publish "A non-empty -Message is required when changes would be committed."
        }
        $Message = Read-Host "Commit message"
    }
    if ($hasCommitChanges -and [string]::IsNullOrWhiteSpace($Message)) {
        Stop-Publish "Commit message cannot be empty."
    }

    if ($DryRun) {
        Write-Host ""
        Write-Step "Dry run only; no files were staged, committed, or pushed."
        if ($hasCommitChanges) {
            if ($useExplicitPaths) {
                Write-Host "Would stage only the explicitly selected paths."
            }
            else {
                Write-Host "Would stage all reviewed tracked changes and new files."
            }
            Write-Host "Would create one commit: $($Message.Trim())"
        }
        if ($localAhead -gt 0 -or $hasCommitChanges) {
            Write-Host "Would push normally to: $Remote/$Branch"
        }
        exit 0
    }

    if (-not $Yes) {
        $answer = Read-Host "Continue with commit/sync to $Remote/$Branch? [y/N]"
        if ($answer -notmatch '^(?i:y|yes)$') {
            Write-Step "Cancelled; no files were staged by this script."
            exit 0
        }
    }

    $createdCommit = $null
    if ($hasCommitChanges) {
        if ($useExplicitPaths) {
            Write-Step "Staging only explicitly selected paths..."
        }
        else {
            Write-Step "Staging reviewed tracked changes and new files..."
        }
        Invoke-Git -Arguments (@("add", "-A", "--") + $selectedPaths) | Out-Null
        Assert-StagedPaths -ExpectedPaths $selectedPaths

        Invoke-Git -Arguments @("diff", "--cached", "--check") | Out-Null
        $stagedPaths = @(Get-StagedPaths)
        if ($stagedPaths.Count -eq 0) {
            Stop-Publish "No changes remained after staging."
        }
        Write-Host ""
        Write-Host "Staged paths:" -ForegroundColor Yellow
        $stagedPaths | ForEach-Object { Write-Host "  $_" }

        Write-Step "Creating commit..."
        Invoke-Git -Arguments @("commit", "-m", $Message.Trim()) | Out-Null
        $createdCommit = Get-GitText -Arguments @("rev-parse", "HEAD")
    }

    $currentRemoteHead = Get-RemoteHead -RemoteName $Remote -BranchName $Branch
    if ($currentRemoteHead -ne $initialRemoteHead) {
        Stop-Publish (
            "Remote branch changed after the initial check. " +
            "Any local commit was kept, but push was stopped."
        )
    }

    $headToPush = Get-GitText -Arguments @("rev-parse", "HEAD")
    Write-Step "Pushing $headToPush to $Remote/$Branch..."
    Invoke-Git -Arguments @(
        "push", $Remote, "HEAD:refs/heads/$Branch"
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
        Stop-Publish "Verification failed: local HEAD does not match $Remote/$Branch."
    }
    if ($upstream -ne "$Remote/$Branch") {
        Stop-Publish "Verification failed: upstream is '$upstream', expected '$Remote/$Branch'."
    }

    Write-Host ""
    Write-Step "Project synchronized successfully."
    if (-not [string]::IsNullOrWhiteSpace($createdCommit)) {
        Write-Host "Created commit: $createdCommit"
        Write-Host "Message: $($Message.Trim())"
    }
    else {
        Write-Host "No new commit was required."
    }
    Write-Host "Target: $upstream"
    $untrackedAfter = @(Get-UntrackedFiles)
    if ($untrackedAfter.Count -gt 0) {
        Write-Host "Untracked files remaining:"
        $untrackedAfter | ForEach-Object { Write-Host "  $_" }
    }
    exit 0
}
catch {
    Write-Host "[publish] ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
