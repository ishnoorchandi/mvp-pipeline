"""Operator Summary — normalizes a run's scattered run_state.json/artifact
fields into the handful of decision-focused facts an operator needs: what
happened, is it safe to build/commit/deliver, what's blocking it, what's the
next safe action, which artifacts matter most.

Covers:
1. Operator summary generation for plan-only run.
2. Operator summary generation for sandbox build run.
3. Operator summary generation for blocked sprint run.
4. Operator summary generation for repo hygiene blocked run.
5. Operator summary generation for interrupted run.
6. Primary artifact selection.
7. Past run filter classification for plan-only.
8. Past run filter classification for sandbox.
9. Past run filter classification for blocked.
10. Past run filter classification for OneHR.
11. Old run without metadata still gets a safe unknown summary.
12. Frontend build success is covered separately by `npm run build` (no JS
    test runner is configured in this repo).

Fixture run folders only — never touches real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend.app as app_mod


def write_artifact(run_dir: Path, name: str, content) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        (run_dir / name).write_text(json.dumps(content), encoding="utf-8")
    else:
        (run_dir / name).write_text(str(content), encoding="utf-8")


# ── 1. Plan-only run ─────────────────────────────────────────────────────────

def test_operator_summary_plan_only_run():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {
            "status": "feature_plan_only_done",
            "mode": "existing_app_upgrade",
            "existing_app_path": "/Users/dev/OneHR-UI",
            "execution_mode": "plan_only",
            "plan_only": True,
            "build_allowed": False,
            "claude_build_allowed": False,
        }
        artifacts = ["feature_sprint_plan.md", "feature_sprint_plan.json", "sprint_quality_gate.md"]
        write_artifact(run_dir, "sprint_quality_gate.json", {
            "summary": {"build_ready_count": 2, "review_required_count": 0, "requires_decomposition_count": 1},
        })
        summary = app_mod.build_operator_run_summary(run_dir, run_state, artifacts)
        assert summary["workflow_type"] == "existing_app_plan"
        assert summary["execution_mode"] == "plan_only"
        assert summary["build_status"] == "not_run"
        assert summary["delivery_status"] == "not_requested"
        assert summary["workspace_mode"] == "planning_only"
        assert summary["sprint_quality_status"] == "has_build_ready_sprints"
        assert summary["current_status"] == "Sprint plan ready"
        assert summary["next_safe_action"] == "Review build-ready sprints"
        assert summary["target_repo_name"] == "OneHR-UI"
        assert "feature_sprint_plan.md" in summary["primary_artifacts"]


# ── 2. Sandbox build run ─────────────────────────────────────────────────────

def test_operator_summary_sandbox_build_run():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {
            "status": "done",
            "mode": "existing_app_upgrade",
            "original_repo_path": "/Users/dev/github-delivery-test",
            "execution_mode": "build",
            "build_workspace_mode": "sandbox",
            "sandbox_workspace": "/Users/dev/mvp-sandboxes/github-delivery-test-run_107",
            "active_build_path": "/Users/dev/mvp-sandboxes/github-delivery-test-run_107",
            "original_repo_modified": False,
            "original_repo_change_check": "passed",
            "regression_status": "PASS",
            "change_boundary_status": "PASS",
        }
        artifacts = [
            "claude_build_output.txt", "sandbox_patch.diff", "sandbox_patch_summary.md",
            "sandbox_changed_files.md", "apply_patch_instructions.md",
        ]
        summary = app_mod.build_operator_run_summary(run_dir, run_state, artifacts)
        assert summary["workflow_type"] == "existing_app_build"
        assert summary["execution_mode"] == "sandbox_build"
        assert summary["build_status"] == "passed"
        assert summary["workspace_mode"] == "sandbox"
        assert summary["repo_health"] == "clean"
        assert summary["current_status"] == "Sandbox build completed"
        assert summary["next_safe_action"] == "Review sandbox patch"
        assert summary["target_repo_name"] == "github-delivery-test"
        assert "sandbox_patch.diff" in summary["primary_artifacts"]
        assert "sandbox_patch_summary.md" in summary["primary_artifacts"]


# ── 3. Blocked sprint run ────────────────────────────────────────────────────

def test_operator_summary_blocked_sprint_run():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {
            "status": "blocked_sprint_not_build_ready",
            "mode": "existing_app_upgrade",
            "existing_app_path": "/Users/dev/OneHR-API",
            "execution_mode": "build",
        }
        artifacts = ["feature_sprint_plan.md", "sprint_quality_gate.md", "decomposition_needed_sprints.md"]
        write_artifact(run_dir, "sprint_quality_gate.json", {
            "summary": {"build_ready_count": 0, "review_required_count": 0, "requires_decomposition_count": 1},
        })
        summary = app_mod.build_operator_run_summary(run_dir, run_state, artifacts)
        assert summary["build_status"] == "blocked"
        assert summary["current_status"] == "Build blocked"
        assert summary["blocking_issue"] == "Selected sprint needs decomposition before build."
        assert summary["next_safe_action"] == "Decompose selected sprint."
        assert summary["sprint_quality_status"] == "needs_decomposition"


# ── 4. Repo hygiene blocked run ──────────────────────────────────────────────

def test_operator_summary_repo_hygiene_blocked_run():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {
            "status": "build_blocked",
            "mode": "existing_app_upgrade",
            "existing_app_path": "/Users/dev/OneHR-UI",
            "execution_mode": "build_blocked",
            "build_gate_reason": "company-protected repo on protected branch — requires a sandbox "
                                  "workspace or prepared feature branch",
            "repo_hygiene_severity": "blocked",
            "repo_hygiene_summary_text": "Dependency folder changes detected under node_modules/venv/vendor.",
            "repo_hygiene_recommended_action": "Resolve dependency folder changes before pulling, building, or committing.",
        }
        artifacts = ["repo_hygiene_summary.md", "repo_hygiene_state.json", "git_sync_report.md"]
        summary = app_mod.build_operator_run_summary(run_dir, run_state, artifacts)
        assert summary["build_status"] == "blocked"
        assert summary["repo_health"] == "dirty_dependency_files"
        assert summary["current_status"] == "Build blocked"
        assert "sandbox" in summary["blocking_issue"].lower()
        assert "repo_hygiene_summary.md" in summary["primary_artifacts"]


# ── 5. Interrupted run ────────────────────────────────────────────────────────

def test_operator_summary_interrupted_run():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {"status": "interrupted", "mode": "existing_app_upgrade", "existing_app_path": "/tmp/x"}
        summary = app_mod.build_operator_run_summary(run_dir, run_state, [])
        assert summary["build_status"] == "interrupted"
        assert summary["current_status"] == "Run interrupted before completion."
        assert summary["next_safe_action"] == "Ignore or rerun."


def test_operator_summary_sandbox_original_repo_modified_warning():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {
            "status": "sandbox_original_repo_modified_warning",
            "mode": "existing_app_upgrade", "existing_app_path": "/tmp/x",
            "build_workspace_mode": "sandbox", "original_repo_modified": True,
            "original_repo_change_check": "failed",
        }
        summary = app_mod.build_operator_run_summary(run_dir, run_state, ["claude_build_output.txt"])
        assert summary["repo_health"] == "blocked"
        assert "unexpectedly" in summary["blocking_issue"].lower()
        assert summary["next_safe_action"] == "Investigate immediately before trusting this run."


# ── 6. Primary artifact selection ───────────────────────────────────────────

def test_primary_artifact_selection_orders_by_priority_and_existence():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {"mode": "existing_app_upgrade", "existing_app_path": "/tmp/x"}
        artifacts = [
            "feature_completion_report.md",  # low priority
            "feature_sprint_plan.md",         # highest priority
            "feature_gap_matrix.md",
            "unrelated_file.txt",
        ]
        summary = app_mod.build_operator_run_summary(run_dir, run_state, artifacts)
        assert summary["primary_artifacts"][0] == "feature_sprint_plan.md"
        assert "feature_gap_matrix.md" in summary["primary_artifacts"]
        assert "unrelated_file.txt" not in summary["primary_artifacts"]


def test_primary_artifact_selection_empty_when_nothing_matches():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        run_state = {}
        summary = app_mod.build_operator_run_summary(run_dir, run_state, ["raw_input.md", "run_state.json"])
        assert summary["primary_artifacts"] == []


# ── 7-10. Past run filter classification ────────────────────────────────────

def test_filter_classification_plan_only():
    with tempfile.TemporaryDirectory() as td:
        run_state = {"mode": "existing_app_upgrade", "execution_mode": "plan_only", "existing_app_path": "/tmp/x"}
        summary = app_mod.build_operator_run_summary(Path(td), run_state, [])
        # Mirrors the frontend's "Plan only" filter: execution_mode plan_only OR workflow_type existing_app_plan.
        assert summary["execution_mode"] == "plan_only" or summary["workflow_type"] == "existing_app_plan"


def test_filter_classification_sandbox():
    with tempfile.TemporaryDirectory() as td:
        run_state = {
            "mode": "existing_app_upgrade", "execution_mode": "build",
            "build_workspace_mode": "sandbox", "existing_app_path": "/tmp/x",
        }
        summary = app_mod.build_operator_run_summary(Path(td), run_state, ["claude_build_output.txt"])
        # Mirrors the frontend's "Sandbox" filter: workspace_mode == sandbox.
        assert summary["workspace_mode"] == "sandbox"


def test_filter_classification_blocked():
    with tempfile.TemporaryDirectory() as td:
        run_state = {"status": "build_blocked", "execution_mode": "build_blocked", "mode": "existing_app_upgrade"}
        summary = app_mod.build_operator_run_summary(Path(td), run_state, [])
        # Mirrors the frontend's "Blocked" filter: build_status/delivery_status/repo_health == blocked.
        assert summary["build_status"] == "blocked"


def test_filter_classification_onehr():
    with tempfile.TemporaryDirectory() as td:
        run_state = {"mode": "existing_app_upgrade", "existing_app_path": "/Users/dev/Projects/OneHR/OneHR-UI"}
        summary = app_mod.build_operator_run_summary(Path(td), run_state, [])
        # Mirrors the frontend's "OneHR" filter: target_repo_name/path contains OneHR.
        assert "onehr" in (summary["target_repo_name"] or "").lower() \
            or "onehr" in (summary["target_repo_path"] or "").lower()


def test_filter_classification_clean():
    with tempfile.TemporaryDirectory() as td:
        run_state = {
            "mode": "existing_app_upgrade", "existing_app_path": "/tmp/x",
            "repo_hygiene_severity": "clean",
        }
        summary = app_mod.build_operator_run_summary(Path(td), run_state, [])
        assert summary["repo_health"] == "clean"
        assert not summary["blocking_issue"]


# ── 11. Old run without metadata still gets a safe unknown summary ─────────

def test_old_run_without_metadata_gets_safe_unknown_summary():
    with tempfile.TemporaryDirectory() as td:
        summary = app_mod.build_operator_run_summary(Path(td), {}, [])
        assert summary["workflow_type"] == "unknown"
        assert summary["execution_mode"] == "unknown"
        assert summary["repo_health"] == "unknown"
        assert summary["workspace_mode"] == "unknown"
        assert summary["safe_to_show"] is True
        assert summary["primary_artifacts"] == []


def test_old_run_with_only_status_field_does_not_crash():
    with tempfile.TemporaryDirectory() as td:
        summary = app_mod.build_operator_run_summary(Path(td), {"status": "done"}, ["raw_input.md"])
        assert summary["current_status"] == "Run complete"


def test_list_runs_and_get_run_attach_operator_summary():
    original_runs_dir = app_mod.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        runs_dir.mkdir()
        app_mod.RUNS_DIR = runs_dir
        run_path = runs_dir / "run_001"
        run_path.mkdir()
        (run_path / "run_state.json").write_text(json.dumps({
            "run_id": "run_001", "status": "feature_plan_only_done",
            "mode": "existing_app_upgrade", "existing_app_path": "/tmp/x",
            "execution_mode": "plan_only", "artifacts": ["feature_sprint_plan.md"],
        }), encoding="utf-8")
        try:
            runs = app_mod.list_runs()
            assert len(runs) == 1
            assert runs[0]["operator_summary"]["execution_mode"] == "plan_only"

            client = app_mod.app.test_client()
            response = client.get("/api/runs/run_001")
            assert response.status_code == 200
            body = response.get_json()
            assert body["operator_summary"]["workflow_type"] == "existing_app_plan"
        finally:
            app_mod.RUNS_DIR = original_runs_dir


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
