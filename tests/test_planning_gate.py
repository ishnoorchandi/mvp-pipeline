"""
Planning Gate — universal pre-build sign-off state model.

Covers:
1.  Raw idea build requires requirements/architecture/global instructions approval.
2.  Existing app upgrade build requires requirements/architecture/global instructions approval.
3.  Written requirements build requires requirements/architecture/global instructions approval.
4.  Plan-only run is allowed to plan without approval and does not build.
5.  Backend inventory/read-only workflow does not require planning approval.
6.  Existing approved requirements + approved architecture + GLOBAL_INSTRUCTIONS.md allows planning gate.
7.  Missing architecture approval blocks build.
8.  Missing GLOBAL_INSTRUCTIONS.md blocks build.
9.  Planning gate fields are written into run_state for build-capable runs.
10. Older run without planning gate fields returns safe unknown/not_applicable values.
11. Backend operator summary includes planning_gate.
12. Frontend build succeeds (validated by npm run build in the validation script).

Fixture repos/runs only — never uses real OneHR repos.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import planning_gate as pg
import backend.app as app_mod
import pipeline_mvp_builder as p


# ── Shared fixture helpers ────────────────────────────────────────────────────

def make_run_dir(tmp: Path, **run_state_extra) -> Path:
    """Create a minimal fixture run directory with run_state.json."""
    run_dir = tmp / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"run_id": "run_001", "status": "done", **run_state_extra}
    (run_dir / "run_state.json").write_text(json.dumps(state))
    return run_dir


def write_requirements_signoff(run_dir: Path, status: str) -> None:
    (run_dir / pg.REQUIREMENTS_SIGNOFF_FILE).write_text(
        json.dumps({"status": status})
    )


def write_architecture_signoff(run_dir: Path, status: str) -> None:
    (run_dir / pg.ARCHITECTURE_SIGNOFF_FILE).write_text(
        json.dumps({"status": status})
    )


def write_global_instructions(run_dir: Path) -> None:
    (run_dir / pg.GLOBAL_INSTRUCTIONS_FILE).write_text(
        "# Global instructions\nFollow all coding standards.\n"
    )


def _git(repo: Path, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def make_git_repo(root: Path, branch: str = "pipeline/feature-x") -> Path:
    repo = root / "fixture_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", branch, str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("# fixture\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


# ── 1. Raw idea build requires approval ───────────────────────────────────────

def test_raw_idea_build_requires_approval():
    gate = pg.build_planning_gate_state(
        entry_point="raw_idea",
        execution_mode="build",
        build_requested=True,
    )
    assert gate["build_requires_approval"] is True
    assert gate["build_allowed_by_planning_gate"] is False
    assert "architecture" in gate["planning_gate_reason"].lower() or "global" in gate["planning_gate_reason"].lower()


# ── 2. Existing app upgrade build requires approval ───────────────────────────

def test_existing_app_upgrade_build_requires_approval():
    gate = pg.build_planning_gate_state(
        entry_point="existing_app_upgrade",
        execution_mode="build",
        build_requested=True,
    )
    assert gate["build_requires_approval"] is True
    assert gate["build_allowed_by_planning_gate"] is False
    assert gate["entry_point"] == "existing_app_upgrade"


# ── 3. Written requirements build requires approval ───────────────────────────

def test_written_requirements_build_requires_approval():
    gate = pg.build_planning_gate_state(
        entry_point="written_requirements",
        execution_mode="build",
        build_requested=True,
    )
    assert gate["build_requires_approval"] is True
    assert gate["build_allowed_by_planning_gate"] is False


# ── 4. Plan-only run is allowed to plan without approval ─────────────────────

def test_plan_only_does_not_require_approval_and_does_not_build():
    gate = pg.build_planning_gate_state(
        entry_point="existing_app_upgrade",
        execution_mode="plan_only",
        build_requested=False,
    )
    assert gate["build_requires_approval"] is False
    # build_allowed_by_planning_gate is False because no build is requested
    assert gate["build_allowed_by_planning_gate"] is False
    assert "plan-only" in gate["planning_gate_reason"].lower()

    # workflow_requires_planning_approval must also be False for plan-only
    assert pg.workflow_requires_planning_approval("existing_app_upgrade", "plan_only", False) is False


# ── 5. Backend inventory / read-only workflow does not require approval ────────

def test_backend_inventory_does_not_require_planning_approval():
    gate = pg.build_planning_gate_state(
        entry_point="backend_inventory",
        execution_mode="inventory",
        build_requested=False,
    )
    assert gate["build_requires_approval"] is False
    assert gate["build_allowed_by_planning_gate"] is True
    assert gate["requirements_status"] == "not_applicable"
    assert gate["architecture_status"] == "not_applicable"
    assert gate["global_instructions_status"] == "not_applicable"
    assert "read-only" in gate["planning_gate_reason"].lower()


def test_backend_safety_does_not_require_planning_approval():
    gate = pg.build_planning_gate_state(
        entry_point="backend_safety",
        execution_mode="plan_only",
        build_requested=False,
    )
    assert gate["build_requires_approval"] is False
    assert gate["build_allowed_by_planning_gate"] is True


# ── 6. Fully approved fixture allows planning gate ────────────────────────────

def test_all_approvals_present_allows_planning_gate():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir, "approved")
        write_architecture_signoff(run_dir, "approved")
        write_global_instructions(run_dir)

        gate = pg.build_planning_gate_state(
            entry_point="existing_app_upgrade",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate["requirements_approved"] is True
        assert gate["architecture_approved"] is True
        assert gate["global_instructions_created"] is True
        assert gate["build_allowed_by_planning_gate"] is True
        assert gate["planning_stage"] == "ready_for_build"


# ── 7. Missing architecture approval blocks build ─────────────────────────────

def test_missing_architecture_approval_blocks_build():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir, "approved")
        # No architecture signoff file → not_started
        write_global_instructions(run_dir)

        gate = pg.build_planning_gate_state(
            entry_point="existing_app_upgrade",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate["architecture_approved"] is False
        assert gate["build_allowed_by_planning_gate"] is False
        assert "architecture" in gate["planning_gate_reason"].lower()


def test_architecture_not_approved_status_blocks_build():
    gate = pg.build_planning_gate_state(
        entry_point="existing_app_upgrade",
        execution_mode="build",
        build_requested=True,
        requirements_status="approved",
        architecture_status="review",  # not yet approved
        global_instructions_status="created",
    )
    assert gate["architecture_approved"] is False
    assert gate["build_allowed_by_planning_gate"] is False
    assert "architecture" in gate["planning_gate_reason"].lower()


# ── 8. Missing GLOBAL_INSTRUCTIONS.md blocks build ────────────────────────────

def test_missing_global_instructions_blocks_build():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir, "approved")
        write_architecture_signoff(run_dir, "approved")
        # No GLOBAL_INSTRUCTIONS.md written

        gate = pg.build_planning_gate_state(
            entry_point="existing_app_upgrade",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate["global_instructions_created"] is False
        assert gate["build_allowed_by_planning_gate"] is False
        assert "global_instructions" in gate["planning_gate_reason"].lower() or "GLOBAL_INSTRUCTIONS" in gate["planning_gate_reason"]


# ── 9. Planning gate fields are written into run_state for build-capable runs ──

def test_planning_gate_fields_written_to_run_state(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = make_git_repo(root)
        monkeypatch.setattr(p, "RUNS_DIR", root / "runs")
        p.RUNS_DIR.mkdir()
        monkeypatch.setattr(p, "gpt", lambda *_a, **_k: "# Generated\n")
        monkeypatch.setattr(p, "gpt4o", lambda *_a, **_k: json.dumps({"sprints": [{
            "sprint_number": 1, "title": "Add filters",
            "goal": "Let users save filters.",
            "features": ["save filter"],
            "completion_criteria": ["A filter can be saved and reloaded."],
            "non_goals": ["No backend changes."],
            "smoke_checks": ["npm run build"],
            "likely_files_created": ["frontend/src/Filters.tsx"],
            "likely_files_modified": ["frontend/src/App.tsx"],
        }]}))

        run_id = p.pipeline_existing_app_upgrade(
            str(app), "add user filter feature", feature_plan_only=True, use_deepseek=False,
        )
        state = p.load_state(run_id)

        # Planning gate fields must be present
        assert "entry_point" in state, "entry_point missing from run_state"
        assert "build_allowed_by_planning_gate" in state, "build_allowed_by_planning_gate missing"
        assert "planning_gate_reason" in state, "planning_gate_reason missing"
        assert state["entry_point"] == "existing_app_upgrade"
        assert state["build_allowed_by_planning_gate"] is False  # plan-only
        assert "plan-only" in state["planning_gate_reason"].lower()


# ── 10. Older run without planning gate fields returns safe defaults ───────────

def test_older_run_without_planning_gate_returns_safe_defaults():
    # Simulate an older run_state that predates planning gate — only has base fields
    old_run_state = {
        "run_id": "run_000",
        "status": "done",
        "mode": "existing_app_upgrade",
        "execution_mode": "plan_only",
        "plan_only": True,
    }
    gate = pg.build_planning_gate_from_run_state(old_run_state)
    # Must not raise; must return a complete dict
    assert "entry_point" in gate
    assert "build_allowed_by_planning_gate" in gate
    assert "planning_gate_reason" in gate
    # Older plan_only run inferred as plan_only → build_allowed should be False
    assert gate["build_allowed_by_planning_gate"] is False


def test_empty_run_state_returns_safe_defaults():
    gate = pg.build_planning_gate_from_run_state({})
    assert gate["entry_point"] == "unknown"
    assert "build_allowed_by_planning_gate" in gate
    # Empty run state → safe/permissive default (unknown → no blocking)
    assert gate["build_allowed_by_planning_gate"] is True


# ── 11. Backend operator summary includes planning_gate ───────────────────────

def test_operator_summary_includes_planning_gate():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {
            "status": "feature_plan_only_done",
            "mode": "existing_app_upgrade",
            "execution_mode": "plan_only",
            "plan_only": True,
            "build_allowed": False,
            "claude_build_allowed": False,
            "entry_point": "existing_app_upgrade",
            "build_allowed_by_planning_gate": False,
            "planning_gate_reason": "Plan-only run; build not requested.",
            "planning_stage": "intake",
            "requirements_status": "not_started",
            "architecture_status": "not_started",
            "global_instructions_status": "not_created",
        }
        summary = app_mod.build_operator_run_summary(run_dir, run_state, [])
        assert "planning_gate" in summary, "operator summary must include planning_gate"
        pg_in_summary = summary["planning_gate"]
        assert pg_in_summary["entry_point"] == "existing_app_upgrade"
        assert pg_in_summary["build_allowed_by_planning_gate"] is False


def test_operator_summary_planning_gate_for_read_only_run():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {
            "status": "backend_inventory_done",
            "backend_inventory_mode": True,
            "entry_point": "backend_inventory",
            "requirements_status": "not_applicable",
            "architecture_status": "not_applicable",
            "global_instructions_status": "not_applicable",
            "build_allowed_by_planning_gate": True,
            "planning_gate_reason": "Planning approval is not required for this read-only workflow.",
        }
        summary = app_mod.build_operator_run_summary(run_dir, run_state, [])
        assert "planning_gate" in summary
        assert summary["planning_gate"]["build_allowed_by_planning_gate"] is True
        assert summary["planning_gate"]["requirements_status"] == "not_applicable"


# ── merge_planning_gate_into_build_gate contract ─────────────────────────────

def test_merge_blocks_build_when_planning_gate_missing_arch():
    """merge_planning_gate_into_build_gate must block claude_build_allowed when gate blocks."""
    build_gate = {
        "execution_mode": "build",
        "plan_only": False,
        "build_allowed": True,
        "claude_build_allowed": True,
        "company_repo_build_allowed": True,
        "reason": "build allowed",
        "build_workspace_mode": "direct",
        "sandbox_requested": False,
        "sandbox_workspace": None,
        "active_build_path": "/tmp/repo",
        "original_repo_path": "/tmp/repo",
    }
    pg_state = pg.build_planning_gate_state(
        entry_point="existing_app_upgrade",
        execution_mode="build",
        build_requested=True,
        requirements_status="approved",
        architecture_status="review",  # not approved yet
        global_instructions_status="created",
    )
    merged = pg.merge_planning_gate_into_build_gate(build_gate, pg_state)
    assert merged["claude_build_allowed"] is False
    assert merged["execution_mode"] == "build_blocked"
    assert "planning_gate" in merged
    assert merged["planning_gate"]["build_allowed_by_planning_gate"] is False


def test_merge_does_not_override_existing_block():
    """When build_gate already blocks, merged gate keeps original reason."""
    blocked_gate = {
        "execution_mode": "build_blocked",
        "plan_only": False,
        "build_allowed": False,
        "claude_build_allowed": False,
        "company_repo_build_allowed": False,
        "reason": "company-protected repo on protected branch",
        "build_workspace_mode": "none",
        "sandbox_requested": False,
        "sandbox_workspace": None,
        "active_build_path": None,
        "original_repo_path": "/tmp/repo",
    }
    pg_state = pg.build_planning_gate_state(
        entry_point="existing_app_upgrade",
        execution_mode="build_blocked",
        build_requested=True,
    )
    merged = pg.merge_planning_gate_into_build_gate(blocked_gate, pg_state)
    # Original block reason preserved
    assert merged["reason"] == "company-protected repo on protected branch"
    assert merged["claude_build_allowed"] is False
    assert "planning_gate" in merged


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
