"""PR branch preparation safety tests.

Uses temporary git repos only. The feature may create/switch a local branch and
create a local commit, but it must never push or open a PR.
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


def make_bare_origin(root: Path) -> Path:
    bare = root / "origin.git"
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


def test_clean_up_to_date_repo_creates_local_branch_without_commit():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(make_bare_origin(root), root)
        out = root / "out"

        state = d.run_prepare_pr_branch(
            repo,
            base_branch="main",
            branch_name="pipeline/example-name",
            pr_title="Example name",
            commit_message="Example name",
            output_dir=out,
        )

        assert state["decision"] == "NO_CHANGES"
        assert state["branch_created"] is True
        assert state["branch_switched"] is True
        assert state["commit_attempted"] is False
        assert state["commit_created"] is False
        assert state["no_push_performed"] is True
        assert state["no_pr_opened"] is True
        assert state["no_reset_stash_clean_performed"] is True
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "pipeline/example-name"
        assert (out / "pr_branch_plan.md").exists()
        assert (out / "pr_branch_state.json").exists()
        assert (out / "local_pr_commit_summary.md").exists()


def test_dirty_base_without_run_boundary_is_blocked_before_branch_creation():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(make_bare_origin(root), root)
        (repo / "README.md").write_text("local edit\n")

        state = d.run_prepare_pr_branch(
            repo,
            base_branch="main",
            branch_name="pipeline/example-name",
            commit_message="Example name",
        )

        assert state["decision"] == "BLOCKED"
        assert state["branch_created"] is False
        assert state["commit_created"] is False
        assert any("base branch is dirty" in reason for reason in state["block_reasons"])
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


def test_run_boundary_allows_intended_dirty_changes_to_commit_on_feature_branch():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(make_bare_origin(root), root)
        run_dir = root / "run"
        run_dir.mkdir()
        (run_dir / "selected_feature_change_boundary.json").write_text(json.dumps({
            "expected_files_create": [],
            "expected_files_modify": ["README.md"],
            "allowed_directories": [],
            "protected_existing_files": [],
            "expected_deletions": [],
        }))
        (repo / "README.md").write_text("intended edit\n")

        state = d.run_prepare_pr_branch(
            repo,
            base_branch="main",
            branch_name="pipeline/readme-edit",
            commit_message="Edit README",
            run_dir=run_dir,
            output_dir=run_dir,
        )

        assert state["decision"] == "COMMITTED_LOCAL"
        assert state["branch_created"] is True
        assert state["commit_attempted"] is True
        assert state["commit_created"] is True
        assert state["commit_hash"]
        assert state["files_committed"] == ["README.md"]
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "pipeline/readme-edit"
        assert _git(repo, "status", "--short").stdout.strip() == ""
        saved = json.loads((run_dir / "pr_branch_state.json").read_text())
        assert saved["decision"] == "COMMITTED_LOCAL"


def test_company_repo_requires_explicit_local_branch_approval():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = clone_repo(
            make_bare_origin(root),
            root,
            push_remote="git@github.com-onehr:OneHR-Interon/OneHR-UI.git",
        )

        blocked = d.run_prepare_pr_branch(
            repo,
            base_branch="main",
            branch_name="pipeline/company-local",
            commit_message="Company local",
        )
        assert blocked["decision"] == "BLOCKED"
        assert any("--allow-company-local-branch" in r for r in blocked["block_reasons"])

        allowed = d.run_prepare_pr_branch(
            repo,
            base_branch="main",
            branch_name="pipeline/company-local",
            commit_message="Company local",
            allow_company_local_branch=True,
        )
        assert allowed["decision"] == "NO_CHANGES"
        assert allowed["branch_created"] is True


def test_existing_app_wrapper_writes_pr_branch_run_state_fields():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p.RUNS_DIR = root / "runs"
        try:
            repo = clone_repo(make_bare_origin(root), root)
            run_id = "run_fixture_pr_branch"
            p.init_run(run_id, "fixture pr branch prep")
            rdir = p.run_dir(run_id)
            (rdir / "selected_feature_change_boundary.json").write_text(json.dumps({
                "expected_files_create": [],
                "expected_files_modify": ["README.md"],
                "allowed_directories": [],
                "protected_existing_files": [],
                "expected_deletions": [],
            }))
            (repo / "README.md").write_text("wrapper edit\n")

            state = p.run_existing_app_pr_branch_prepare(
                run_id,
                repo,
                base_branch="main",
                branch_name="pipeline/wrapper-edit",
                commit_message="Wrapper edit",
            )

            run_state = p.load_state(run_id)
            assert state["decision"] == "COMMITTED_LOCAL"
            assert run_state["pr_branch_decision"] == "COMMITTED_LOCAL"
            assert run_state["pr_branch_name"] == "pipeline/wrapper-edit"
            assert run_state["pr_commit_hash"] == state["commit_hash"]
            assert run_state["pr_branch_artifacts"] == d.PR_BRANCH_PREP_ARTIFACTS
            assert "pr_branch_plan.md" in run_state["artifacts"]
            assert "pr_branch_state.json" in run_state["artifacts"]
            assert "local_pr_commit_summary.md" in run_state["artifacts"]
        finally:
            p.RUNS_DIR = original_runs_dir


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
