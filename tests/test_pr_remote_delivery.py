"""PR remote delivery safety tests.

Uses local fixture repos and local bare remotes only. Never touches OneHR or a
real GitHub remote.
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
    return subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def make_bare_origin(root: Path, owner_repo: str = "owner/repo") -> Path:
    bare = root / f"{owner_repo}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    seed = root / "_seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Test User")
    (seed / "README.md").write_text("hello\n")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial commit")
    _git(seed, "push", "origin", "main")
    return bare


def clone_repo(bare: Path, root: Path, push_remote: str | None = None) -> Path:
    repo = root / "repo"
    subprocess.run(["git", "clone", str(bare), str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    if push_remote:
        _git(repo, "remote", "set-url", "--push", "origin", push_remote)
    return repo


def prepare_feature_commit(repo: Path, branch: str = "pipeline/test-pr-delivery") -> str:
    _git(repo, "checkout", "-b", branch)
    (repo / "feature.txt").write_text("feature\n")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature commit")
    return branch


def test_personal_sandbox_allowlist_pushes_feature_branch():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bare = make_bare_origin(root, "owner/repo")
        repo = clone_repo(bare, root)
        branch = prepare_feature_commit(repo)

        state = d.run_pr_remote_delivery(
            repo,
            base_branch="main",
            branch_name=branch,
            push_pr_branch=True,
            sandbox_allowlist={"owner/repo"},
            output_dir=root / "out",
        )

        assert state["decision"] == "PUSHED_BRANCH"
        assert state["push_attempted"] is True
        assert state["push_succeeded"] is True
        assert state["push_command"] == f"git push -u origin {branch}"
        assert "--force" not in state["push_command"]
        assert " main" not in state["push_command"]
        assert state["no_main_push_performed"] is True
        assert state["no_force_push_performed"] is True
        assert _git(repo, "rev-parse", "--verify", f"origin/{branch}").returncode == 0
        assert (root / "out" / "pr_remote_state.json").exists()


def test_missing_sandbox_allowlist_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(make_bare_origin(root, "owner/repo"), root)
        branch = prepare_feature_commit(repo)

        state = d.run_pr_remote_delivery(
            repo,
            base_branch="main",
            branch_name=branch,
            push_pr_branch=True,
            sandbox_allowlist={"someone/else"},
        )

        assert state["decision"] == "BLOCKED"
        assert state["push_attempted"] is False
        assert any("allowlist" in reason for reason in state["block_reasons"])


def test_company_repo_without_company_approval_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(
            make_bare_origin(root, "owner/repo"),
            root,
            push_remote="git@github.com-onehr:OneHR-Interon/OneHR-UI.git",
        )
        branch = prepare_feature_commit(repo)

        state = d.run_pr_remote_delivery(
            repo,
            base_branch="main",
            branch_name=branch,
            push_pr_branch=True,
            sandbox_allowlist={"owner/repo"},
        )

        assert state["decision"] == "BLOCKED"
        assert state["push_attempted"] is False
        assert any("--allow-company-pr" in reason for reason in state["block_reasons"])


def test_current_branch_main_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(make_bare_origin(root, "owner/repo"), root)

        state = d.run_pr_remote_delivery(
            repo,
            base_branch="main",
            branch_name="pipeline/test-pr-delivery",
            push_pr_branch=True,
            sandbox_allowlist={"owner/repo"},
        )

        assert state["decision"] == "BLOCKED"
        assert state["push_attempted"] is False
        assert any("current branch" in reason or "protected/base" in reason for reason in state["block_reasons"])


def test_dirty_working_tree_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(make_bare_origin(root, "owner/repo"), root)
        branch = prepare_feature_commit(repo)
        (repo / "dirty.txt").write_text("dirty\n")

        state = d.run_pr_remote_delivery(
            repo,
            base_branch="main",
            branch_name=branch,
            push_pr_branch=True,
            sandbox_allowlist={"owner/repo"},
        )

        assert state["decision"] == "BLOCKED"
        assert state["push_attempted"] is False
        assert any("working tree is dirty" in reason for reason in state["block_reasons"])


def test_branch_with_no_commits_ahead_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(make_bare_origin(root, "owner/repo"), root)
        branch = "pipeline/no-commits"
        _git(repo, "checkout", "-b", branch)

        state = d.run_pr_remote_delivery(
            repo,
            base_branch="main",
            branch_name=branch,
            push_pr_branch=True,
            sandbox_allowlist={"owner/repo"},
        )

        assert state["decision"] == "BLOCKED"
        assert state["push_attempted"] is False
        assert any("at least one commit" in reason for reason in state["block_reasons"])


def test_open_pr_without_gh_writes_manual_instructions(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(make_bare_origin(root, "github.com/owner/repo"), root)
        branch = prepare_feature_commit(repo)
        monkeypatch.setattr(d.shutil, "which", lambda _: None)

        state = d.run_pr_remote_delivery(
            repo,
            base_branch="main",
            branch_name=branch,
            pr_title="Test PR delivery",
            pr_body="Test PR created by MVP Pipeline sandbox run.",
            push_pr_branch=True,
            open_pr=True,
            sandbox_allowlist={"owner/repo"},
            output_dir=root / "out",
        )

        assert state["decision"] == "MANUAL_PR_REQUIRED"
        assert state["push_succeeded"] is True
        assert state["pr_attempted"] is False
        assert state["pr_created"] is False
        assert state["manual_pr_url"] == f"https://github.com/owner/repo/compare/main...{branch}?expand=1"
        assert state["manual_pr_instructions"]
        report = (root / "out" / "pr_remote_delivery_report.md").read_text()
        assert "Manual PR Instructions" in report
        assert state["manual_pr_url"] in report


def test_existing_app_wrapper_writes_remote_delivery_state_fields():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p.RUNS_DIR = root / "runs"
        try:
            repo = clone_repo(make_bare_origin(root, "owner/repo"), root)
            branch = prepare_feature_commit(repo)
            run_id = "run_fixture_pr_remote"
            p.init_run(run_id, "fixture pr remote")

            state = p.run_existing_app_pr_remote_delivery(
                run_id,
                repo,
                base_branch="main",
                branch_name=branch,
                push_pr_branch=True,
                sandbox_allowlist={"owner/repo"},
            )

            run_state = p.load_state(run_id)
            assert state["decision"] == "PUSHED_BRANCH"
            assert run_state["pr_remote_decision"] == "PUSHED_BRANCH"
            assert run_state["pr_remote_branch"] == branch
            assert run_state["pr_remote_artifacts"] == d.PR_REMOTE_ARTIFACTS
            assert "pr_remote_delivery_report.md" in run_state["artifacts"]
            assert "pr_remote_state.json" in run_state["artifacts"]
            assert "pr_push_result.md" in run_state["artifacts"]
            assert "pr_create_result.md" in run_state["artifacts"]
            saved = json.loads((p.run_dir(run_id) / "pr_remote_state.json").read_text())
            assert saved["push_command"] == f"git push -u origin {branch}"
        finally:
            p.RUNS_DIR = original_runs_dir


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
