"""Pull Request Delivery Plan — planning layer for collaborative repos (e.g.
OneHR/OneATS) where the correct workflow is: (1) sync with origin/<base_branch>,
(2) create a feature branch LATER, (3) commit LATER, (4) push only the feature
branch LATER, (5) open a PR LATER.

This feature NEVER pushes anything and NEVER creates a PR — it only produces a
read-only PR readiness plan (pr_delivery_plan.md / pr_state.json).

Proves:
1. Standalone PR plan writes pr_delivery_plan.md and pr_state.json.
2. A clean, up-to-date, non-company repo produces PR readiness "ready".
3. A dirty repo produces a "blocked" readiness.
4. A repo behind origin/main produces a "warning" readiness (sync first).
5. A company-protected repo (OneHR-Interon-style remote) always reports direct
   push to the base branch as blocked.
6. A disabled push URL (DISABLED_DO_NOT_PUSH_COMPANY_REPO) does not prevent plan
   creation — it adds a warning about a future approval/setup step.
7. An unsafe requested branch name is sanitized (not silently accepted, not a crash)
   into a safe suggested_branch.
8. Missing prior safety artifacts (no run_dir / no run context) are reported as
   "missing" / "not_applicable", never a crash.
9. Existing App Upgrade's PR-plan helper writes pr_delivery_plan.md / pr_state.json
   and pr_plan_* run_state fields when used against a target repo.

Uses temporary fixture git repos only. Never touches real OneHR repos, and never
creates a branch, commit, push, or PR.
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


def clone_repo(bare: Path, root: Path, name: str = "repo", push_remote: str | None = None) -> Path:
    repo = root / name
    subprocess.run(["git", "clone", str(bare), str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    if push_remote:
        _git(repo, "remote", "set-url", "--push", "origin", push_remote)
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


# ── 1. Standalone PR plan writes both artifacts ─────────────────────────────

def test_standalone_pr_plan_writes_both_artifacts():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        out_dir = Path(td) / "out"

        plan = d.run_pr_delivery_plan(repo, base_branch="main", output_dir=out_dir)

        assert (out_dir / "pr_delivery_plan.md").exists()
        assert (out_dir / "pr_state.json").exists()
        content = (out_dir / "pr_delivery_plan.md").read_text(encoding="utf-8")
        assert "This is a plan only." in content
        assert "No branch was created." in content
        assert "No commit was made." in content
        assert "No push was attempted." in content
        assert "No PR was opened." in content
        data = json.loads((out_dir / "pr_state.json").read_text(encoding="utf-8"))
        assert data["pr_readiness"] == plan["pr_readiness"]
        assert data["plan_only"] is True


# ── 2. Clean up-to-date non-company repo => ready ───────────────────────────

def test_clean_up_to_date_repo_is_ready():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))

        plan = d.analyze_pr_delivery_plan(repo, base_branch="main")
        assert plan["pr_readiness"] == "ready"
        assert plan["pr_creation_allowed_later"] is True
        assert plan["is_up_to_date"] is True
        assert plan["is_dirty"] is False
        assert plan["block_reasons"] == []
        assert plan["suggested_branch"].startswith("pipeline/")
        assert "pull --ff-only" not in plan["recommended_next_action"]


# ── 3. Dirty repo => blocked ─────────────────────────────────────────────────

def test_dirty_repo_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))
        add_dirty_file(repo, "wip.txt", "local work\n")

        plan = d.analyze_pr_delivery_plan(repo, base_branch="main")
        assert plan["pr_readiness"] == "blocked"
        assert plan["pr_creation_allowed_later"] is False
        assert plan["is_dirty"] is True
        assert any("dirty" in r for r in plan["block_reasons"])


# ── 4. Behind repo => warning (sync first) ──────────────────────────────────

def test_behind_repo_is_warning():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo_a = clone_repo(bare, Path(td), "repo-a")
        repo_b = clone_repo(bare, Path(td), "repo-b")
        commit_and_push(repo_b, "feature.txt", "from another dev\n", "another dev's change")

        plan = d.analyze_pr_delivery_plan(repo_a, base_branch="main")
        assert plan["sync_status"] == "behind"
        assert plan["pr_readiness"] == "warning"
        assert plan["pr_creation_allowed_later"] is True
        assert any("behind" in w for w in plan["warnings"])
        assert plan["recommended_next_action"] == (
            "Sync first with: git fetch origin && git pull --ff-only origin main"
        )


# ── 5. Company-protected repo always blocks direct push to base branch ─────

def test_company_protected_repo_blocks_direct_push_to_main():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(
            bare, Path(td),
            push_remote="git@github.com-onehr:OneHR-Interon/OneHR-UI.git",
        )

        plan = d.analyze_pr_delivery_plan(repo, base_branch="main")
        assert plan["repo_type"] == "company-protected"
        assert plan["is_company_repo"] is True
        assert plan["direct_push_to_main_blocked"] is True
        assert plan["future_push_approval_required"] is True
        # Company repo alone is a warning, not a fatal block, when otherwise clean.
        assert plan["pr_readiness"] in ("warning", "pr_workflow_required")
        assert any("company" in w.lower() for w in plan["warnings"])
        assert plan["sync_status"] == "up_to_date"
        assert plan["is_dirty"] is False
        assert "pull --ff-only" not in plan["recommended_next_action"]
        assert plan["recommended_next_action"].startswith("Repo is up to date.")


# ── 6. Disabled push URL doesn't prevent plan creation ──────────────────────

def test_disabled_push_url_does_not_prevent_plan_creation():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td), push_remote=d.DISABLED_PUSH_MARKER)
        out_dir = Path(td) / "out"

        plan = d.run_pr_delivery_plan(repo, base_branch="main", output_dir=out_dir)
        assert (out_dir / "pr_delivery_plan.md").exists()
        assert (out_dir / "pr_state.json").exists()
        assert plan["pr_readiness"] != "blocked"
        assert any("future approval" in w.lower() or "explicit" in w.lower() for w in plan["warnings"])
        assert plan["push_url"] == d.DISABLED_PUSH_MARKER


# ── 7. Unsafe branch name is sanitized, not silently accepted or a crash ───

def test_unsafe_branch_name_is_sanitized():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))

        plan = d.analyze_pr_delivery_plan(repo, base_branch="main", branch_name="main")
        assert plan["branch_name_safe"] is False
        assert plan["branch_was_sanitized"] is True
        assert plan["requested_branch"] == "main"
        assert d.is_safe_branch_name(plan["suggested_branch"])
        assert plan["suggested_branch"] != "main"
        assert any("sanitized" in w.lower() for w in plan["warnings"])

        plan2 = d.analyze_pr_delivery_plan(repo, base_branch="main", branch_name="bad branch name!!")
        assert plan2["branch_was_sanitized"] is True
        assert d.is_safe_branch_name(plan2["suggested_branch"])


# ── 8. Missing prior safety artifacts are reported, never a crash ──────────

def test_missing_safety_artifacts_reported_not_crashed():
    with tempfile.TemporaryDirectory() as td:
        bare = make_bare_origin(Path(td))
        repo = clone_repo(bare, Path(td))

        plan = d.analyze_pr_delivery_plan(repo, base_branch="main", run_dir=None)
        assert plan["boundary_check_status"] == "not_applicable"
        assert plan["smoke_mutation_status"] == "not_applicable"
        assert plan["delivery_safety_status"] == "missing"

        empty_run_dir = Path(td) / "empty_run"
        empty_run_dir.mkdir()
        plan2 = d.analyze_pr_delivery_plan(repo, base_branch="main", run_dir=empty_run_dir)
        assert plan2["boundary_check_status"] == "not_applicable"
        assert plan2["smoke_mutation_status"] == "not_applicable"
        assert plan2["delivery_safety_status"] == "missing"


# ── 9. Existing App Upgrade writes PR plan artifacts ────────────────────────

def test_existing_app_upgrade_pr_plan_helper_writes_artifacts_and_state():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        p.RUNS_DIR = runs_dir
        try:
            bare = make_bare_origin(Path(td))
            repo = clone_repo(bare, Path(td))

            run_id = "run_fixture_pr_plan"
            p.init_run(run_id, "fixture existing app upgrade")

            plan = p.run_existing_app_pr_delivery_plan(
                run_id, repo, base_branch="main", pr_title="Fix candidate dashboard loading",
            )
            assert plan["pr_readiness"] == "ready"

            rdir = p.run_dir(run_id)
            assert (rdir / "pr_delivery_plan.md").exists()
            assert (rdir / "pr_state.json").exists()

            state = p.load_state(run_id)
            assert state["pr_plan_status"] == "ready"
            assert state["pr_plan_branch"] == plan["suggested_branch"]
            assert state["pr_plan_artifacts"] == ["pr_delivery_plan.md", "pr_state.json"]
            assert "pr_delivery_plan.md" in state["artifacts"]
            assert "pr_state.json" in state["artifacts"]
        finally:
            p.RUNS_DIR = original_runs_dir


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
