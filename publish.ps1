[CmdletBinding()]
param(
    [string]$Remote = "origin",
    [string]$Branch = "PDSchedule",
    [string]$WorkflowMessage = "chore: update PDSchedule publishing workflow",
    [string]$RefactorMessage = "refactor: remove knowledge modules and retain PD scheduling",
    [switch]$Yes,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$script:GitExecutable = $null
$workflowPlanPath = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String(
        "cGxhbi/ku6PnoIHkv67mlLnliY3orqHliJLnoa7orqTmtYHnqIsubWQ="
    )
)
$taskPlanPath = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String(
        "cGxhbi/pgILphY1QRFNjaGVkdWxl5Y+M6ISa5pys5Y+R5biD6K6h5YiSLm1k"
    )
)
$script:WorkflowPaths = @(
    $workflowPlanPath,
    "create_pdschedule.ps1",
    "publish.ps1"
)
$script:ForbiddenPaths = @(
    "run_arrival_experiments.sh",
    $taskPlanPath
)

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

function Get-WorkflowStatus {
    return @(Get-GitLines -Arguments (
        @("-c", "core.quotepath=false", "status", "--porcelain=v1", "--") +
        $script:WorkflowPaths
    ))
}

function Get-TrackedWorktreeChanges {
    return @(Get-GitLines -Arguments @(
        "-c", "core.quotepath=false",
        "diff", "--name-only", "--diff-filter=ACDMRTUXB"
    ))
}

function Get-StagedPaths {
    return @(Get-GitLines -Arguments @(
        "-c", "core.quotepath=false",
        "diff", "--cached", "--name-only", "--diff-filter=ACDMRTUXB"
    ))
}

function Assert-NoForbiddenStagedPath {
    $stagedPaths = @(Get-StagedPaths)
    foreach ($path in $stagedPaths) {
        if ($script:ForbiddenPaths -contains $path) {
            Stop-Publish "Forbidden path entered the index: $path"
        }
    }
}

function Assert-WorkflowStage {
    $stagedPaths = @(Get-StagedPaths)
    if ($stagedPaths.Count -eq 0) {
        Stop-Publish "No workflow changes were staged for the first commit."
    }
    foreach ($path in $stagedPaths) {
        if ($script:WorkflowPaths -notcontains $path) {
            Stop-Publish "Unexpected path in the workflow commit: $path"
        }
    }
    Assert-NoForbiddenStagedPath
}

function Assert-RefactorStage {
    $stagedPaths = @(Get-StagedPaths)
    if ($stagedPaths.Count -eq 0) {
        Stop-Publish "No tracked changes were staged for the refactor commit."
    }
    foreach ($path in $stagedPaths) {
        if ($script:WorkflowPaths -contains $path) {
            Stop-Publish "Workflow path entered the refactor commit: $path"
        }
    }
    Assert-NoForbiddenStagedPath
}

function Get-CommitSubject {
    param([string]$Revision)
    $result = Invoke-Git -Arguments @("show", "-s", "--format=%s", $Revision) -AllowFailure -Quiet
    if ($result.ExitCode -ne 0) {
        return $null
    }
    return (($result.Output -join "`n").Trim())
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
    if ([string]::IsNullOrWhiteSpace($WorkflowMessage)) {
        Stop-Publish "Workflow commit message cannot be empty."
    }
    if ([string]::IsNullOrWhiteSpace($RefactorMessage)) {
        Stop-Publish "Refactor commit message cannot be empty."
    }

    Set-Location -LiteralPath $PSScriptRoot
    $repositoryRoot = Get-GitText -Arguments @("rev-parse", "--show-toplevel")
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
            "Branch $Branch has no upstream. Run create_pdschedule.ps1 in a clean repository " +
            "to create and first-push origin/$Branch."
        )
    }
    $upstream = (($upstreamResult.Output -join "`n").Trim())
    if ($upstream -ne "$Remote/$Branch") {
        Stop-Publish "Upstream is '$upstream'; expected '$Remote/$Branch'."
    }

    $initialRemoteHead = Get-RemoteHead -RemoteName $Remote -BranchName $Branch
    if ([string]::IsNullOrWhiteSpace($initialRemoteHead)) {
        Stop-Publish (
            "Remote branch does not exist: $Remote/$Branch. " +
            "Run create_pdschedule.ps1 first."
        )
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
            "Run without -DryRun once authentication and fetch are available."
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
            "Remote branch contains commits not integrated into local HEAD, or histories diverged. " +
            "This script will not merge, rebase, or force-push."
        )
    }

    foreach ($path in $script:WorkflowPaths) {
        if (-not (Test-Path -LiteralPath $path)) {
            Stop-Publish "Required workflow file is missing: $path"
        }
    }

    $workflowStatus = @(Get-WorkflowStatus)
    $trackedChanges = @(Get-TrackedWorktreeChanges)
    $refactorChanges = @(
        $trackedChanges | Where-Object { $script:WorkflowPaths -notcontains $_ }
    )
    $untracked = @(Get-GitLines -Arguments @(
        "-c", "core.quotepath=false",
        "ls-files", "--others", "--exclude-standard"
    ))

    if ($untracked.Count -gt 0) {
        Write-Host ""
        Write-Host "Untracked files will remain excluded:" -ForegroundColor Yellow
        $untracked | ForEach-Object { Write-Host "  $_" }
    }

    $headSubject = Get-CommitSubject -Revision "HEAD"
    $parentSubject = Get-CommitSubject -Revision "HEAD^"
    $workflowPending = $workflowStatus.Count -gt 0
    $refactorPending = $refactorChanges.Count -gt 0
    $workflowAlreadyCommitted = (
        (-not $workflowPending) -and
        ($headSubject -eq $WorkflowMessage)
    )
    $bothAlreadyCommitted = (
        (-not $workflowPending) -and
        (-not $refactorPending) -and
        ($headSubject -eq $RefactorMessage) -and
        ($parentSubject -eq $WorkflowMessage)
    )

    if ($workflowPending -and $headSubject -eq $WorkflowMessage) {
        Stop-Publish (
            "The workflow commit already exists, but workflow files changed again. " +
            "Review these changes manually before retrying."
        )
    }
    if ($workflowPending -and -not $refactorPending) {
        Stop-Publish "Workflow changes exist, but no refactor changes are available for the second commit."
    }
    if (-not $workflowPending -and $refactorPending -and -not $workflowAlreadyCommitted) {
        Stop-Publish (
            "Refactor changes exist, but HEAD is not the expected workflow commit. " +
            "The script cannot safely infer the first commit boundary."
        )
    }
    if (-not $workflowPending -and -not $refactorPending -and -not $bothAlreadyCommitted) {
        Stop-Publish "No expected two-commit publication state was found."
    }

    Write-Step "Repository: $repositoryRoot"
    Write-Step "Target: $Remote/$Branch"
    Write-Step "Remote base: $initialRemoteHead"
    if ($workflowPending) {
        Write-Host ""
        Write-Host "First commit ($WorkflowMessage):" -ForegroundColor Yellow
        $workflowStatus | ForEach-Object { Write-Host "  $_" }
    }
    elseif ($workflowAlreadyCommitted) {
        Write-Step "First commit already exists at HEAD; it will not be duplicated."
    }
    if ($refactorPending) {
        Write-Host ""
        Write-Host "Second commit ($RefactorMessage):" -ForegroundColor Yellow
        $refactorChanges | ForEach-Object { Write-Host "  $_" }
    }
    elseif ($bothAlreadyCommitted) {
        Write-Step "Both expected commits already exist locally; only push verification remains."
    }

    if ($DryRun) {
        Write-Host ""
        Write-Step "Dry run only; no files were staged, committed, or pushed."
        if ($workflowPending) {
            Write-Host "Would stage only the three workflow files and create the first commit."
        }
        if ($refactorPending) {
            Write-Host "Would stage tracked changes only and create the second commit."
        }
        Write-Host "Would push normally to: $Remote/$Branch"
        exit 0
    }

    if (-not $Yes) {
        $answer = Read-Host "Create the two scoped commits and push to $Remote/$Branch? [y/N]"
        if ($answer -notmatch '^(?i:y|yes)$') {
            Write-Step "Cancelled; no files were staged by this script."
            exit 0
        }
    }

    $workflowCommit = $null
    $refactorCommit = $null

    if ($workflowPending) {
        Write-Step "Staging only workflow files..."
        Invoke-Git -Arguments (@("add", "--") + $script:WorkflowPaths) | Out-Null
        Assert-WorkflowStage
        Invoke-Git -Arguments @("diff", "--cached", "--check") | Out-Null
        Write-Step "Creating workflow commit..."
        Invoke-Git -Arguments @("commit", "-m", $WorkflowMessage.Trim()) | Out-Null
        $workflowCommit = Get-GitText -Arguments @("rev-parse", "HEAD")
    }
    else {
        $workflowCommit = Get-GitText -Arguments @("rev-parse", "HEAD")
    }

    if ($refactorPending) {
        Assert-EmptyIndex
        Write-Step "Staging remaining tracked changes only..."
        Invoke-Git -Arguments @("add", "-u", "--", ".") | Out-Null
        Assert-RefactorStage
        Invoke-Git -Arguments @("diff", "--cached", "--check") | Out-Null
        Write-Step "Creating refactor commit..."
        Invoke-Git -Arguments @("commit", "-m", $RefactorMessage.Trim()) | Out-Null
        $refactorCommit = Get-GitText -Arguments @("rev-parse", "HEAD")
    }
    else {
        $refactorCommit = Get-GitText -Arguments @("rev-parse", "HEAD")
        $workflowCommit = Get-GitText -Arguments @("rev-parse", "HEAD^")
    }

    $finalWorkflowSubject = Get-CommitSubject -Revision $workflowCommit
    $finalRefactorSubject = Get-CommitSubject -Revision $refactorCommit
    if ($finalWorkflowSubject -ne $WorkflowMessage.Trim()) {
        Stop-Publish "Workflow commit subject verification failed."
    }
    if ($finalRefactorSubject -ne $RefactorMessage.Trim()) {
        Stop-Publish "Refactor commit subject verification failed."
    }

    $currentRemoteHead = Get-RemoteHead -RemoteName $Remote -BranchName $Branch
    if ($currentRemoteHead -ne $initialRemoteHead) {
        Stop-Publish (
            "Remote branch changed after the initial check. Local commits were kept, " +
            "but push was stopped."
        )
    }

    Write-Step "Pushing both commits to $Remote/$Branch..."
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
    Write-Step "Published successfully."
    Write-Host "Workflow commit: $workflowCommit"
    Write-Host "Refactor commit: $refactorCommit"
    Write-Host "Target: $upstream"
    if ($untracked.Count -gt 0) {
        Write-Host "Untracked files remain excluded:"
        $untracked | ForEach-Object { Write-Host "  $_" }
    }
    exit 0
}
catch {
    Write-Host "[publish] ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
