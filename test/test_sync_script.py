import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT_DIR / "sync.sh"
BASH = shutil.which("bash")
if BASH is None and os.name == "nt":
    for candidate in (Path("D:/Git/bin/bash.exe"), Path("C:/Program Files/Git/bin/bash.exe")):
        if candidate.exists():
            BASH = str(candidate)
            break


def run(command, cwd, check=True):
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"Command failed ({result.returncode}): {command}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class SyncScriptStaticTest(unittest.TestCase):
    def test_script_documents_safe_sync_primitives(self):
        content = SYNC_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("git stash push --include-untracked", content)
        self.assertIn("git merge --ff-only", content)
        self.assertIn("git merge --no-edit -X theirs", content)
        self.assertIn('git restore --source="$REMOTE_REF" --staged --worktree', content)
        self.assertIn("chmod u+x", content)
        self.assertNotIn("git reset --hard", content)
        self.assertNotIn("git clean", content)
        self.assertNotIn("git push", content)


@unittest.skipUnless(BASH, "Bash is required for sync.sh integration tests")
class SyncScriptIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.deploy = self.root / "deploy"

        run(["git", "init", "--bare", str(self.remote)], self.root)
        run(["git", "init", "-b", "p2p-flexpd", str(self.seed)], self.root)
        self._configure_repo(self.seed)
        (self.seed / "shared.txt").write_text("line 1 base\nline 2 base\n", encoding="utf-8")
        (self.seed / "tracked.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        run(["git", "add", "shared.txt", "tracked.sh"], self.seed)
        run(["git", "commit", "-m", "base"], self.seed)
        run(["git", "remote", "add", "origin", str(self.remote)], self.seed)
        run(["git", "push", "-u", "origin", "p2p-flexpd"], self.seed)

        run(
            ["git", "clone", "--branch", "p2p-flexpd", str(self.remote), str(self.deploy)],
            self.root,
        )
        self._configure_repo(self.deploy)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _configure_repo(repo):
        run(["git", "config", "user.name", "Sync Test"], repo)
        run(["git", "config", "user.email", "sync-test@example.com"], repo)

    def _remote_commit(self, path, content, message):
        (self.seed / path).write_text(content, encoding="utf-8")
        run(["git", "add", path], self.seed)
        run(["git", "commit", "-m", message], self.seed)
        run(["git", "push", "origin", "p2p-flexpd"], self.seed)
        return run(["git", "rev-parse", "HEAD"], self.seed).stdout.strip()

    def _sync(self):
        return run([BASH, str(SYNC_SCRIPT), "origin", "p2p-flexpd"], self.deploy, check=False)

    def test_fast_forward_restores_non_conflicting_uncommitted_changes(self):
        (self.deploy / "local-only.txt").write_text("local\n", encoding="utf-8")
        remote_head = self._remote_commit("remote-only.txt", "remote\n", "remote change")

        result = self._sync()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.deploy / "local-only.txt").read_text(encoding="utf-8"), "local\n")
        self.assertEqual((self.deploy / "remote-only.txt").read_text(encoding="utf-8"), "remote\n")
        self.assertEqual(run(["git", "rev-parse", "HEAD"], self.deploy).stdout.strip(), remote_head)
        self.assertEqual(run(["git", "stash", "list"], self.deploy).stdout.strip(), "")

    def test_stash_conflict_keeps_remote_file_and_backup_stash(self):
        (self.deploy / "shared.txt").write_text("local worktree\n", encoding="utf-8")
        self._remote_commit("shared.txt", "remote worktree\n", "remote shared change")

        result = self._sync()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual((self.deploy / "shared.txt").read_text(encoding="utf-8"), "remote worktree\n")
        self.assertIn("sync.sh backup", run(["git", "stash", "list"], self.deploy).stdout)

    def test_same_file_clean_merge_still_uses_complete_remote_version(self):
        (self.deploy / "shared.txt").write_text(
            "line 1 local\nline 2 base\n",
            encoding="utf-8",
        )
        self._remote_commit(
            "shared.txt",
            "line 1 base\nline 2 remote\n",
            "remote changes another line",
        )

        result = self._sync()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(
            (self.deploy / "shared.txt").read_text(encoding="utf-8"),
            "line 1 base\nline 2 remote\n",
        )
        self.assertIn("sync.sh backup", run(["git", "stash", "list"], self.deploy).stdout)

    def test_untracked_collision_keeps_remote_file_and_backup_stash(self):
        (self.deploy / "collision.txt").write_text("local untracked\n", encoding="utf-8")
        self._remote_commit("collision.txt", "remote tracked\n", "remote adds collision")

        result = self._sync()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual((self.deploy / "collision.txt").read_text(encoding="utf-8"), "remote tracked\n")
        self.assertIn("sync.sh backup", run(["git", "stash", "list"], self.deploy).stdout)

    def test_diverged_commits_are_merged_with_remote_content_priority(self):
        (self.deploy / "shared.txt").write_text("local commit\n", encoding="utf-8")
        run(["git", "add", "shared.txt"], self.deploy)
        run(["git", "commit", "-m", "local commit"], self.deploy)
        local_commit = run(["git", "rev-parse", "HEAD"], self.deploy).stdout.strip()
        self._remote_commit("shared.txt", "remote commit\n", "remote commit")

        result = self._sync()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.deploy / "shared.txt").read_text(encoding="utf-8"), "remote commit\n")
        ancestor = run(
            ["git", "merge-base", "--is-ancestor", local_commit, "HEAD"],
            self.deploy,
            check=False,
        )
        self.assertEqual(ancestor.returncode, 0)

    def test_wrong_branch_is_rejected_without_stashing(self):
        run(["git", "switch", "-c", "other"], self.deploy)
        (self.deploy / "local.txt").write_text("keep\n", encoding="utf-8")

        result = self._sync()

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.deploy / "local.txt").exists())
        self.assertEqual(run(["git", "stash", "list"], self.deploy).stdout.strip(), "")

    def test_in_progress_merge_is_rejected(self):
        (self.deploy / "shared.txt").write_text("local merge\n", encoding="utf-8")
        run(["git", "add", "shared.txt"], self.deploy)
        run(["git", "commit", "-m", "local merge side"], self.deploy)
        self._remote_commit("shared.txt", "remote merge\n", "remote merge side")
        run(["git", "fetch", "origin", "p2p-flexpd"], self.deploy)
        merge = run(
            ["git", "merge", "origin/p2p-flexpd"],
            self.deploy,
            check=False,
        )
        self.assertNotEqual(merge.returncode, 0)

        result = self._sync()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("operation already in progress", result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not available on Windows")
    def test_permission_repair_only_changes_tracked_shell_scripts(self):
        tracked_script = self.deploy / "tracked.sh"
        untracked_script = self.deploy / "untracked.sh"
        untracked_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        tracked_script.chmod(0o600)
        untracked_script.chmod(0o600)

        result = self._sync()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(tracked_script.stat().st_mode & 0o100, 0)
        self.assertEqual(untracked_script.stat().st_mode & 0o100, 0)


if __name__ == "__main__":
    unittest.main()
