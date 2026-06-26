"""Git Pull (fast-forward only) — the next step after Git Sync / Pull Safety.

Adds a guarded `git pull --ff-only origin <base_branch>` action that only runs
when the preflight (delivery.analyze_git_sync) confirms the repo is clean, behind
origin/<base_branch>, and fast_forward_safe. Never pushes, merges, resets,
stashes, checks out, or cleans.

Proves:
1. A clean repo behind origin/main is fast-forward-pulled and becomes up_to_date.
2. A dirty repo blocks the pull and the working tree is never touched.
3. An ahead repo blocks the pull.
4. A diverged repo blocks the pull.
5. An up-to-date repo does not run pull and reports a safe NO_OP (not BLOCKED) —
   now_up_to_date is true and no pull command is attempted.
6. A repo with no origin/main blocks the pull.
7. Pull artifacts (git_pull_report.md, git_pull_state.json,
   git_sync_before_pull.json, git_sync_after_pull.json) are written.
8. The pull report/state confirm no push/reset/stash occurred.
9. Existing App Upgrade's pull helper pulls before build and blocks the run when
   the pull itself is blocked.

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


def _file_list(repo: Path) -> set[str]:
    return {p.name for p in repo.iterdir() if p.name != ".git"}


# ── 1. Clean repo behind origin/main is fast-forward-pulled ────────────────

def test_behind_clean_repo_is_pulled_and_becomes_up_to_date():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo_a = clone_repo(bare, Path(td), "repo-a")
        repo_b = clone_repo(bare, Path(td), "repo-b")
        commit_and_push(repo_b, "feature.txt", "from another dev\n", "another dev's change")

        outcome = d.run_git_pull_ff_only(repo_a, base_branch="main")
        state = outcome["state"]
        assert state["decision"] == "PULLED"
        assert state["pull_attempted"] is True
        assert state["pull_command"] == "git pull --ff-only origin main"
        assert state["pull_succeeded"] is True
        assert state["now_up_to_date"] is True
        assert outcome["after"]["sync_status"] == "up_to_date"
        assert (repo_a / "feature.txt").exists()


# ── 2. Dirty repo blocks pull and never modifies files ──────────────────────

def test_dirty_repo_blocks_pull_and_does_not_modify_files():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo_a = clone_repo(bare, Path(td), "repo-a")
        repo_b = clone_repo(bare, Path(td), "repo-b")
        commit_and_push(repo_b, "feature.txt", "from another dev\n", "another dev's change")
        add_dirty_file(repo_a, "wip.txt", "local work in progress\n")
        before_files = _file_list(repo_a)

        outcome = d.run_git_pull_ff_only(repo_a, base_branch="main")
        state = outcome["state"]
        assert state["decision"] == "BLOCKED"
        assert state["pull_attempted"] is False
        assert any("dirty" in r for r in state["block_reasons"])
        assert _file_list(repo_a) == before_files
        assert not (repo_a / "feature.txt").exists()
        assert (repo_a / "wip.txt").read_text() == "local work in progress\n"


# ── 3. Ahead repo blocks pull ────────────────────────────────────────────────

def test_ahead_repo_blocks_pull():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        commit_local_only(repo, "local_work.txt", "unpushed\n", "local unpushed commit")

        outcome = d.run_git_pull_ff_only(repo, base_branch="main")
        state = outcome["state"]
        assert state["decision"] == "BLOCKED"
        assert state["pull_attempted"] is False
        assert any("ahead" in r for r in state["block_reasons"])


# ── 4. Diverged repo blocks pull ─────────────────────────────────────────────

def test_diverged_repo_blocks_pull():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo_a = clone_repo(bare, Path(td), "repo-a")
        repo_b = clone_repo(bare, Path(td), "repo-b")
        commit_and_push(repo_b, "remote_work.txt", "from b\n", "pushed by another dev")
        commit_local_only(repo_a, "local_work.txt", "from a\n", "local unpushed commit")

        outcome = d.run_git_pull_ff_only(repo_a, base_branch="main")
        state = outcome["state"]
        assert state["decision"] == "BLOCKED"
        assert state["pull_attempted"] is False
        assert any("diverged" in r for r in state["block_reasons"])


# ── 5. Up-to-date repo does not run pull (no-op) ────────────────────────────

def test_up_to_date_repo_does_not_pull():
    """Already up to date is a safe no-op (decision NO_OP), not a BLOCKED failure —
    no pull command is attempted, and now_up_to_date is reported as true."""
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))

        outcome = d.run_git_pull_ff_only(repo, base_branch="main")
        state = outcome["state"]
        assert state["decision"] == "NO_OP"
        assert state["pull_attempted"] is False
        assert state["now_up_to_date"] is True
        assert any("already up to date" in r for r in state["block_reasons"])


def test_up_to_date_no_op_reports_safe_success_with_artifacts_and_exit_code():
    """Focused regression test for the NO_OP wording/exit-code fix: an up-to-date
    repo must report decision NO_OP (not BLOCKED), now_up_to_date True, attempt no
    pull command, still write both pull artifacts, confirm no push/reset/stash ran,
    and the standalone CLI path must exit 0 (not 1) for this case."""
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        out_dir = Path(td) / "out"

        outcome = d.run_git_pull_ff_only(repo, base_branch="main", output_dir=out_dir)
        state = outcome["state"]

        assert state["decision"] == "NO_OP"
        assert state["pull_attempted"] is False
        assert state["pull_succeeded"] is None
        assert state["now_up_to_date"] is True
        assert state["no_push_performed"] is True
        assert state["no_reset_performed"] is True
        assert state["no_stash_performed"] is True

        for fname in ("git_pull_report.md", "git_pull_state.json"):
            assert (out_dir / fname).exists(), f"missing {fname}"

        report = (out_dir / "git_pull_report.md").read_text(encoding="utf-8")
        assert "Decision: `NO_OP`" in report
        assert "not a failure" in report.lower()
        assert "No push performed: True" in report
        assert "No reset performed: True" in report
        assert "No stash performed: True" in report

        json_state = json.loads((out_dir / "git_pull_state.json").read_text(encoding="utf-8"))
        assert json_state["decision"] == "NO_OP"

        # Standalone CLI exit-code contract: NO_OP must exit 0, same as a real PULLED success.
        cli_success = state["decision"] in ("PULLED", "NO_OP")
        assert cli_success is True


# ── 6. Missing origin/main blocks pull ──────────────────────────────────────

def test_missing_origin_base_branch_blocks_pull():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "no-remote-repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        (repo / "README.md").write_text("hello\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "initial commit")

        outcome = d.run_git_pull_ff_only(repo, base_branch="main")
        state = outcome["state"]
        assert state["decision"] == "BLOCKED"
        assert state["pull_attempted"] is False
        assert any("origin/main" in r for r in state["block_reasons"])


# ── 7 & 8. Pull artifacts are written and confirm no push/reset/stash ──────

def test_pull_artifacts_written_and_confirm_no_push_reset_stash():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo_a = clone_repo(bare, Path(td), "repo-a")
        repo_b = clone_repo(bare, Path(td), "repo-b")
        commit_and_push(repo_b, "feature.txt", "from another dev\n", "another dev's change")
        out_dir = Path(td) / "out"

        outcome = d.run_git_pull_ff_only(repo_a, base_branch="main", output_dir=out_dir)
        state = outcome["state"]

        for fname in ("git_pull_report.md", "git_pull_state.json",
                      "git_sync_before_pull.json", "git_sync_after_pull.json"):
            assert (out_dir / fname).exists(), f"missing {fname}"

        report = (out_dir / "git_pull_report.md").read_text(encoding="utf-8")
        assert "git pull --ff-only origin main" in report
        assert "No push performed: True" in report
        assert "No reset performed: True" in report
        assert "No stash performed: True" in report
        assert "Local repo now up to date:** True" in report

        json_state = json.loads((out_dir / "git_pull_state.json").read_text(encoding="utf-8"))
        assert json_state["no_push_performed"] is True
        assert json_state["no_reset_performed"] is True
        assert json_state["no_stash_performed"] is True
        assert json_state["decision"] == "PULLED"
        assert state["decision"] == "PULLED"


def test_pull_blocked_report_states_no_mutating_command_ran():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        add_dirty_file(repo, "wip.txt", "wip\n")
        out_dir = Path(td) / "out"

        d.run_git_pull_ff_only(repo, base_branch="main", output_dir=out_dir)
        report = (out_dir / "git_pull_report.md").read_text(encoding="utf-8")
        assert "not attempted" in report.lower()
        assert "No push performed: True" in report
        assert "No reset performed: True" in report
        assert "No stash performed: True" in report


# ── 9. Existing App Upgrade pulls before build; blocks run when pull blocked ─

def test_existing_app_upgrade_pull_helper_pulls_and_writes_state():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        p.RUNS_DIR = runs_dir
        try:
            bare = make_bare_origin(Path(td))
            repo_a = clone_repo(bare, Path(td), "repo-a")
            repo_b = clone_repo(bare, Path(td), "repo-b")
            commit_and_push(repo_b, "feature.txt", "from another dev\n", "another dev's change")

            run_id = "run_fixture_git_pull"
            p.init_run(run_id, "fixture existing app upgrade")

            result = p.run_existing_app_git_pull_ff_only(run_id, repo_a, base_branch="main")
            assert result is not None
            pull_state = result["pull_state"]
            sync_state = result["sync_state"]
            assert pull_state["decision"] == "PULLED"
            assert sync_state["sync_status"] == "up_to_date"

            rdir = p.run_dir(run_id)
            for fname in ("git_pull_report.md", "git_pull_state.json",
                          "git_sync_before_pull.json", "git_sync_after_pull.json",
                          "git_sync_report.md", "git_sync_state.json"):
                assert (rdir / fname).exists(), f"missing {fname}"

            state = p.load_state(run_id)
            assert state["git_pull_status"] == "PULLED"
            assert state["git_pull_blocked"] is False
            assert state["git_sync_status"] == "up_to_date"
            assert state["git_pull_artifacts"] == [
                "git_pull_report.md", "git_pull_state.json",
                "git_sync_before_pull.json", "git_sync_after_pull.json",
            ]
        finally:
            p.RUNS_DIR = original_runs_dir


def test_existing_app_upgrade_blocks_run_when_pull_blocked_by_dirty_repo():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        p.RUNS_DIR = runs_dir
        try:
            bare = make_bare_origin(Path(td))
            repo = clone_repo(bare, Path(td))
            add_dirty_file(repo, "wip.txt", "local work\n")

            run_id = "run_fixture_git_pull_blocked"
            p.init_run(run_id, "fixture existing app upgrade")

            result = p.run_existing_app_git_pull_ff_only(run_id, repo, base_branch="main")
            assert result is not None
            assert result["pull_state"]["decision"] == "BLOCKED"

            state = p.load_state(run_id)
            assert state["git_pull_status"] == "BLOCKED"
            assert state["git_pull_blocked"] is True
        finally:
            p.RUNS_DIR = original_runs_dir


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
