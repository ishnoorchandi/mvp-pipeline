"""Git Sync / Pull Safety — read-only foundation for collaborative existing app
repos (e.g. OneHR/OneATS) where other developers are constantly pushing.

This is NOT a blind `git pull`. Proves the safe fetch/status/preflight layer:
1. A clean repo up to date with origin/main reports up_to_date and is not blocked.
2. A repo behind origin/main reports behind, and fast-forward is safe if clean.
3. A dirty repo blocks pull.
4. A repo ahead of origin/main blocks pull.
5. A diverged repo blocks pull.
6. A repo with no origin/main reports unknown and is blocked.
7. Denied dirty paths (e.g. .env) block pull even on an otherwise-safe repo.
8. git_sync_report.md / git_sync_state.json are written by run_git_sync_check.
9. Existing App Upgrade's git-sync helper writes git_sync_* artifacts and
   run_state fields when the target is a git repo.

Uses temporary fixture git repos only. Never touches real OneHR repos.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery as d
import pipeline_mvp_builder as p


def _git(repo: Path, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def make_bare_origin(root: Path, name: str = "origin.git") -> Path:
    """Bare repo standing in for a remote (e.g. GitHub), seeded with one commit on main."""
    bare = root / name
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    seed = root / "_seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Test User")
    (seed / "README.md").write_text("hello\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "initial commit")
    _git(seed, "push", "origin", "main")
    return bare


def clone_repo(bare: Path, root: Path, name: str = "repo") -> Path:
    repo = root / name
    subprocess.run(["git", "clone", str(bare), str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    return repo


def add_dirty_file(repo: Path, relpath: str, content: str = "x\n"):
    p_ = repo / relpath
    p_.parent.mkdir(parents=True, exist_ok=True)
    p_.write_text(content)


def commit_and_push(repo: Path, relpath: str, content: str, message: str):
    add_dirty_file(repo, relpath, content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    _git(repo, "push", "origin", "main")


def commit_local_only(repo: Path, relpath: str, content: str, message: str):
    add_dirty_file(repo, relpath, content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


# ── 1. Clean repo up to date with origin/main ───────────────────────────────

def test_clean_repo_up_to_date():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        sync = d.analyze_git_sync(repo, base_branch="main")
        assert sync["sync_status"] == "up_to_date"
        assert sync["commits_ahead"] == 0
        assert sync["commits_behind"] == 0
        assert sync["pull_blocked"] is False
        assert sync["is_dirty"] is False
        assert sync["origin_base_exists"] is True


# ── 2. Repo behind origin/main → behind + safe fast-forward if clean ───────

def test_repo_behind_reports_behind_and_safe_fast_forward():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo_a = clone_repo(bare, Path(td), "repo-a")
        repo_b = clone_repo(bare, Path(td), "repo-b")
        commit_and_push(repo_b, "feature.txt", "new\n", "add feature from another dev")

        sync = d.analyze_git_sync(repo_a, base_branch="main")
        assert sync["sync_status"] == "behind"
        assert sync["commits_behind"] == 1
        assert sync["commits_ahead"] == 0
        assert sync["fast_forward_safe"] is True
        assert sync["pull_blocked"] is False
        assert sync["recommended_command"] == "git fetch origin && git pull --ff-only origin main"
        assert sync["build_should_proceed"] == "warn"


# ── 3. Dirty repo blocks pull ───────────────────────────────────────────────

def test_dirty_repo_blocks_pull():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        add_dirty_file(repo, "uncommitted.txt", "wip\n")

        sync = d.analyze_git_sync(repo, base_branch="main")
        assert sync["is_dirty"] is True
        assert sync["pull_blocked"] is True
        assert sync["fast_forward_safe"] is False
        assert sync["build_should_proceed"] == "no"
        assert any("dirty" in r for r in sync["block_reasons"])


# ── 4. Repo ahead of origin/main blocks pull ────────────────────────────────

def test_ahead_repo_blocks_pull():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        commit_local_only(repo, "local_work.txt", "wip\n", "local unpushed commit")

        sync = d.analyze_git_sync(repo, base_branch="main")
        assert sync["sync_status"] == "ahead"
        assert sync["commits_ahead"] == 1
        assert sync["pull_blocked"] is True
        assert sync["fast_forward_safe"] is False
        assert any("ahead" in r for r in sync["block_reasons"])


# ── 5. Diverged repo blocks pull ────────────────────────────────────────────

def test_diverged_repo_blocks_pull():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo_a = clone_repo(bare, Path(td), "repo-a")
        repo_b = clone_repo(bare, Path(td), "repo-b")
        commit_and_push(repo_b, "remote_work.txt", "from b\n", "pushed by another dev")
        commit_local_only(repo_a, "local_work.txt", "from a\n", "local unpushed commit")

        sync = d.analyze_git_sync(repo_a, base_branch="main")
        assert sync["sync_status"] == "diverged"
        assert sync["commits_ahead"] == 1
        assert sync["commits_behind"] == 1
        assert sync["pull_blocked"] is True
        assert sync["fast_forward_safe"] is False
        assert any("diverged" in r for r in sync["block_reasons"])


# ── 6. Missing origin/main → unknown + blocked ──────────────────────────────

def test_missing_origin_base_branch_reports_unknown_and_blocked():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "no-remote-repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        (repo / "README.md").write_text("hello\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "initial commit")

        sync = d.analyze_git_sync(repo, base_branch="main", skip_fetch=True)
        assert sync["origin_base_exists"] is False
        assert sync["sync_status"] == "unknown"
        assert sync["pull_blocked"] is True
        assert any("origin/main" in r for r in sync["block_reasons"])


# ── 7. Denied dirty paths block pull even on an otherwise up-to-date repo ──

def test_denied_dirty_paths_block_pull():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        add_dirty_file(repo, ".env", "SECRET=1\n")

        sync = d.analyze_git_sync(repo, base_branch="main")
        assert sync["denied_paths_dirty"] is True
        assert ".env" in sync["denied_dirty_paths"]
        assert sync["pull_blocked"] is True
        assert any("denied paths are dirty" in r for r in sync["block_reasons"])


# ── 8. git_sync_report.md / git_sync_state.json are written ────────────────

def test_git_sync_reports_are_written():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        out_dir = Path(td) / "out"
        sync = d.run_git_sync_check(repo, base_branch="main", output_dir=out_dir)

        md_path = out_dir / "git_sync_report.md"
        json_path = out_dir / "git_sync_state.json"
        assert md_path.exists()
        assert json_path.exists()
        content = md_path.read_text(encoding="utf-8")
        assert str(repo.resolve()) in content
        assert "up_to_date" in content
        assert "Safe to pull" in content
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["sync_status"] == sync["sync_status"]


# ── 9. Existing App Upgrade's git-sync helper writes artifacts + run_state ──

def test_existing_app_upgrade_git_sync_helper_writes_artifacts_and_state():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        p.RUNS_DIR = runs_dir
        try:
            bare = make_bare_origin(Path(td))
            repo = clone_repo(bare, Path(td))
            run_id = "run_fixture_git_sync"
            p.init_run(run_id, "fixture existing app upgrade")

            sync_state = p.run_existing_app_git_sync_check(run_id, repo, base_branch="main")
            assert sync_state is not None
            assert sync_state["sync_status"] == "up_to_date"

            rdir = p.run_dir(run_id)
            assert (rdir / "git_sync_report.md").exists()
            assert (rdir / "git_sync_state.json").exists()

            state = p.load_state(run_id)
            assert state["git_sync_status"] == "up_to_date"
            assert state["git_sync_blocked"] is False
            assert "up_to_date" in state["git_sync_summary"]
            assert state["git_sync_artifacts"] == ["git_sync_report.md", "git_sync_state.json"]
            assert "git_sync_report.md" in state["artifacts"]
            assert "git_sync_state.json" in state["artifacts"]
        finally:
            p.RUNS_DIR = original_runs_dir


def test_existing_app_upgrade_git_sync_helper_returns_none_for_non_git_repo():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        p.RUNS_DIR = runs_dir
        try:
            non_git_app = Path(td) / "plain-app"
            non_git_app.mkdir()
            run_id = "run_fixture_no_git"
            p.init_run(run_id, "fixture existing app upgrade")

            result = p.run_existing_app_git_sync_check(run_id, non_git_app, base_branch="main")
            assert result is None
            rdir = p.run_dir(run_id)
            assert not (rdir / "git_sync_report.md").exists()
        finally:
            p.RUNS_DIR = original_runs_dir


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
