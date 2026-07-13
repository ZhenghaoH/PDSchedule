#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE="${1:-origin}"
BRANCH="${2:-p2p-flexpd}"
BACKUP_STASH_COMMIT=""
BACKUP_STASH_REF=""
RESTORE_HAD_CONFLICTS=0
START_HEAD=""
REMOTE_REF=""
declare -A LOCAL_CHANGED_PATHS=()
declare -A REMOTE_PRIORITY_PATHS=()

log() {
    printf '[sync] %s\n' "$*"
}

warn() {
    printf '[sync] WARNING: %s\n' "$*" >&2
}

die() {
    printf '[sync] ERROR: %s\n' "$*" >&2
    exit 1
}

valid_git_name() {
    local value="$1"
    [[ -n "$value" ]] \
        && [[ "$value" != -* ]] \
        && [[ "$value" != *".."* ]] \
        && [[ "$value" != *"@{"* ]] \
        && [[ "$value" != *"\\"* ]] \
        && [[ "$value" =~ ^[A-Za-z0-9._/-]+$ ]]
}

git_path_exists() {
    local git_path
    git_path="$(git rev-parse --git-path "$1")"
    [[ -e "$git_path" ]]
}

ensure_no_git_operation_in_progress() {
    local marker
    for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply; do
        if git_path_exists "$marker"; then
            die "Git operation already in progress ($marker). Finish or abort it first."
        fi
    done
}

find_stash_ref() {
    local target_commit="$1"
    git stash list --format='%gd %H' \
        | awk -v target="$target_commit" '$2 == target { print $1; exit }'
}

collect_local_changed_paths() {
    local path
    while IFS= read -r -d '' path; do
        LOCAL_CHANGED_PATHS["$path"]=1
    done < <(
        git diff --name-only -z
        git diff --cached --name-only -z
        git ls-files --others --exclude-standard -z
    )
}

collect_remote_priority_paths() {
    local merge_base path
    merge_base="$(git merge-base "$START_HEAD" "$REMOTE_REF")"
    while IFS= read -r -d '' path; do
        if [[ -n "${LOCAL_CHANGED_PATHS[$path]+present}" ]]; then
            REMOTE_PRIORITY_PATHS["$path"]=1
        fi
    done < <(git diff --name-only -z "$merge_base..$REMOTE_REF")
}

restore_remote_path() {
    local path="$1"
    if git cat-file -e "${REMOTE_REF}:${path}" 2>/dev/null; then
        git restore --source="$REMOTE_REF" --staged --worktree -- "$path"
    else
        git rm -f --ignore-unmatch -- "$path"
    fi
}

resolve_merge_conflicts_with_remote() {
    local -a conflicts=()
    local path
    mapfile -d '' conflicts < <(git diff --name-only --diff-filter=U -z)
    ((${#conflicts[@]} > 0)) || return 1

    warn "Resolving ${#conflicts[@]} merge conflict(s) with the remote version."
    for path in "${conflicts[@]}"; do
        if git cat-file -e ":3:${path}" 2>/dev/null; then
            git checkout --theirs -- "$path"
            git add -- "$path"
        else
            git rm -f --ignore-unmatch -- "$path"
        fi
        log "Remote version selected: $path"
    done

    [[ -z "$(git diff --name-only --diff-filter=U)" ]]
}

restore_stashed_changes() {
    [[ -n "$BACKUP_STASH_COMMIT" ]] || return 0

    log "Restoring local uncommitted changes from $BACKUP_STASH_REF."
    local apply_succeeded=0
    if git stash apply "$BACKUP_STASH_COMMIT"; then
        apply_succeeded=1
    fi

    if ((apply_succeeded)) && ((${#REMOTE_PRIORITY_PATHS[@]} == 0)); then
        git reset --quiet HEAD -- .
        git stash drop "$BACKUP_STASH_REF" >/dev/null
        BACKUP_STASH_COMMIT=""
        BACKUP_STASH_REF=""
        log "Local changes restored without conflicts."
        return 0
    fi

    RESTORE_HAD_CONFLICTS=1
    local -a conflicts=()
    local path
    mapfile -d '' conflicts < <(git diff --name-only --diff-filter=U -z)
    for path in "${conflicts[@]}"; do
        restore_remote_path "$path"
        warn "Remote version kept for local stash conflict: $path"
    done

    for path in "${!REMOTE_PRIORITY_PATHS[@]}"; do
        restore_remote_path "$path"
        warn "Remote version kept because both sides changed this path: $path"
    done
    git reset --quiet HEAD -- .

    warn "Some local changes conflicted with the synchronized tree."
    warn "Remote versions are active; the original local content remains in $BACKUP_STASH_REF."
    warn "Inspect it with: git stash show -p $BACKUP_STASH_REF"
}

repair_shell_permissions() {
    local -a shell_files=()
    mapfile -d '' shell_files < <(git ls-files -z -- '*.sh')
    if ((${#shell_files[@]} > 0)); then
        chmod u+x -- "${shell_files[@]}"
    fi
    if [[ -f "$REPO_ROOT/sync.sh" ]]; then
        chmod u+x -- "$REPO_ROOT/sync.sh"
    fi
    log "Executable permission repaired for ${#shell_files[@]} tracked Shell script(s)."
}

valid_git_name "$REMOTE" || die "Invalid remote name: $REMOTE"
valid_git_name "$BRANCH" || die "Invalid branch name: $BRANCH"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || die "Run this script inside a Git worktree."
cd "$REPO_ROOT"

ensure_no_git_operation_in_progress
git remote get-url "$REMOTE" >/dev/null 2>&1 \
    || die "Remote does not exist: $REMOTE"

CURRENT_BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" \
    || die "Detached HEAD is not supported."
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] \
    || die "Current branch is $CURRENT_BRANCH; expected $BRANCH."

log "Repository: $REPO_ROOT"
log "Synchronizing $CURRENT_BRANCH with $REMOTE/$BRANCH"
START_HEAD="$(git rev-parse HEAD)"

if [[ -n "$(git status --porcelain=v1 --untracked-files=normal)" ]]; then
    collect_local_changed_paths
    STASH_MESSAGE="sync.sh backup $(date '+%Y-%m-%d %H:%M:%S')"
    git stash push --include-untracked --message "$STASH_MESSAGE" >/dev/null
    BACKUP_STASH_COMMIT="$(git rev-parse refs/stash)"
    BACKUP_STASH_REF="$(find_stash_ref "$BACKUP_STASH_COMMIT")"
    [[ -n "$BACKUP_STASH_REF" ]] || die "Could not identify the backup stash."
    log "Saved local uncommitted changes in $BACKUP_STASH_REF ($BACKUP_STASH_COMMIT)."
fi

if ! git fetch "$REMOTE" \
    "refs/heads/$BRANCH:refs/remotes/$REMOTE/$BRANCH"; then
    warn "Fetch failed. Restoring the saved local changes."
    restore_stashed_changes || true
    exit 1
fi

REMOTE_REF="refs/remotes/$REMOTE/$BRANCH"
git show-ref --verify --quiet "$REMOTE_REF" \
    || die "Fetched branch is unavailable as $REMOTE_REF."
collect_remote_priority_paths
if ((${#REMOTE_PRIORITY_PATHS[@]} > 0)); then
    warn "${#REMOTE_PRIORITY_PATHS[@]} locally changed path(s) also changed remotely; remote files will win."
fi

read -r LOCAL_AHEAD REMOTE_AHEAD < <(
    git rev-list --left-right --count "HEAD...$REMOTE_REF"
)
log "Commit difference: local ahead=$LOCAL_AHEAD, remote ahead=$REMOTE_AHEAD"

if ((REMOTE_AHEAD > 0)); then
    if ((LOCAL_AHEAD == 0)); then
        git merge --ff-only "$REMOTE_REF" \
            || die "Fast-forward failed. Local changes remain saved in $BACKUP_STASH_REF."
    else
        log "Local and remote commits diverged; merging remote history with remote conflict priority."
        if ! git merge --no-edit -X theirs "$REMOTE_REF"; then
            if ! resolve_merge_conflicts_with_remote; then
                warn "The merge could not be resolved safely."
                warn "Finish or abort the merge manually; local uncommitted changes remain in $BACKUP_STASH_REF."
                exit 1
            fi
            git commit --no-edit \
                || die "Conflicts were resolved, but the required merge commit could not be created."
        fi
    fi
else
    log "No remote commits need to be applied."
fi

restore_stashed_changes
repair_shell_permissions

if ((RESTORE_HAD_CONFLICTS)); then
    warn "Synchronization used remote versions for conflicts. Review $BACKUP_STASH_REF before deleting it."
    exit 2
fi

log "Synchronization completed successfully."
