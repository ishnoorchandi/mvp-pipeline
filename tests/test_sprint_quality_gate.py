"""Sprint Quality Gate — evaluates each generated Existing App Upgrade feature
sprint for build-readiness BEFORE it can be selected for Step 12 (Claude Code
build). Planning/safety only: never runs Claude Code, never changes build
execution behavior beyond blocking sprints that are too vague or too broad to
safely build in one step.

Covers:
1. Frontend-only bounded sprint with files + acceptance criteria is build-ready.
2. Backend API sprint without endpoint list requires decomposition.
3. Database/schema/migration sprint requires decomposition.
4. Auth/RBAC/security sprint requires decomposition.
5. Broad "full platform" sprint requires decomposition.
6. Sprint with no likely files and no acceptance criteria requires decomposition.
7. Overlap sprint with matched files shows overlap and can be build-ready if
   otherwise scoped.
8. Appended/auto-expanded roadmap bucket is marked requires_decomposition.
9. sprint_quality_gate.md/json artifacts are written.
10. Build-ready and decomposition-needed markdown files are written.
11. UI/build API does not allow non-build-ready sprint to build (backend guard:
    select_feature_sprint + the /api/runs continuation endpoint).
12. Older runs without quality metadata still render without crashing.

Fixtures only — never touches real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p
import backend.app as app_mod


def make_sprint(sprint_number=1, **overrides) -> dict:
    base = {
        "sprint_number": sprint_number,
        "title": "Untitled Sprint",
        "goal": "Untitled goal",
        "features": [],
        "completion_criteria": [],
        "likely_files_created": [],
        "likely_files_modified": [],
        "non_goals": [],
        "smoke_checks": [],
        "depends_on": [0],
        "overlap_warnings": [],
        "overlap_matched_files": [],
        "auto_expanded": False,
    }
    base.update(overrides)
    return base


# ── 1. Frontend-only bounded sprint with files + acceptance criteria ───────

def test_frontend_bounded_sprint_is_build_ready():
    sprint = make_sprint(
        title="Requisition Management Dashboard",
        goal="Add a dashboard page so users can view open requisitions.",
        likely_files_created=["src/components/dashboard.tsx"],
        likely_files_modified=["src/pages/Home.tsx"],
        completion_criteria=["Dashboard page renders the list of open requisitions already available in the app."],
        non_goals=["No changes to backend endpoints."],
        smoke_checks=["npm run build"],
    )
    result = p.evaluate_sprint_quality(sprint)
    assert result["build_ready"] is True
    assert result["requires_decomposition"] is False
    assert result["risk_level"] == "low"
    assert result["quality_score"] >= 75
    assert "src/components/dashboard.tsx" in result["likely_files"]


# ── 2. Backend API sprint without endpoint list requires decomposition ─────

def test_backend_sprint_without_endpoints_requires_decomposition():
    sprint = make_sprint(
        title="Add Backend API for Orders",
        goal="Implement backend API support for managing orders.",
        features=["Backend API support"],
    )
    result = p.evaluate_sprint_quality(sprint)
    assert result["requires_decomposition"] is True
    assert result["build_ready"] is False
    assert result["risk_level"] == "high"
    assert any("endpoint" in r.lower() for r in result["reasons"])
    assert any("endpoint" in r.lower() or "api" in r.lower() for r in result["required_refinement"])


# ── 3. Database/schema/migration sprint requires decomposition ─────────────

def test_database_schema_sprint_requires_decomposition():
    sprint = make_sprint(
        title="Backend API and Database Schema",
        goal="Add new database schema and a migration for orders, plus a backend API.",
        likely_files_modified=["backend/models.py"],
    )
    result = p.evaluate_sprint_quality(sprint)
    assert result["requires_decomposition"] is True
    assert result["build_ready"] is False
    assert result["risk_level"] == "high"
    assert any("database" in r.lower() or "schema" in r.lower() for r in result["reasons"])
    assert any("migration" in item.lower() for item in result["required_refinement"])


# ── 4. Auth/RBAC/security sprint requires decomposition ────────────────────

def test_auth_rbac_sprint_requires_decomposition():
    sprint = make_sprint(
        title="Add Role-Based Access Control",
        goal="Implement RBAC permissions for admin users across the app.",
    )
    result = p.evaluate_sprint_quality(sprint)
    assert result["requires_decomposition"] is True
    assert result["build_ready"] is False
    assert result["risk_level"] == "high"
    assert any("auth" in r.lower() or "security" in r.lower() for r in result["reasons"])


# ── 5. Broad "full platform" sprint requires decomposition ─────────────────

def test_full_platform_sprint_requires_decomposition():
    sprint = make_sprint(
        title="Complete Platform Overhaul",
        goal="Implement the full, complete, entire platform-wide admin rewrite covering all areas.",
    )
    result = p.evaluate_sprint_quality(sprint)
    assert result["requires_decomposition"] is True
    assert result["build_ready"] is False
    assert result["risk_level"] == "high"


# ── 6. No likely files and no acceptance criteria requires decomposition ───

def test_no_files_no_acceptance_criteria_requires_decomposition():
    sprint = make_sprint(title="Improve Things", goal="Make the app better somehow.")
    result = p.evaluate_sprint_quality(sprint)
    assert result["requires_decomposition"] is True
    assert result["build_ready"] is False
    assert any("no likely files" in r.lower() for r in result["reasons"])
    assert any("no specific acceptance" in r.lower() for r in result["reasons"])


# ── 7. Overlap sprint with matched files — overlap shown, still build-ready ─

def test_overlap_sprint_with_matched_files_can_be_build_ready():
    sprint = make_sprint(
        title="Extend Dashboard With Saved Filters",
        goal="Extend the existing dashboard page to support saving filters.",
        likely_files_modified=["src/components/dashboard.tsx"],
        completion_criteria=["A saved filter can be created and reloaded from the dashboard."],
        non_goals=["No backend changes."],
        overlap_warnings=["Sprint 1 ('Extend Dashboard...') overlaps with existing dashboard functionality."],
        overlap_matched_files=["src/components/dashboard.tsx"],
    )
    result = p.evaluate_sprint_quality(sprint)
    assert result["has_overlap"] is True
    assert result["matched_existing_files"] == ["src/components/dashboard.tsx"]
    assert result["build_ready"] is True
    assert result["requires_decomposition"] is False


def test_overlap_expected_but_no_matched_files_reduces_confidence():
    sprint = make_sprint(
        title="Extend Reporting",
        goal="Extend reporting with new filters.",
        likely_files_modified=["src/components/reports.tsx"],
        completion_criteria=["A report can be filtered by date range and saved."],
        overlap_warnings=["Sprint 2 overlaps with existing reporting functionality."],
        overlap_matched_files=[],
    )
    result = p.evaluate_sprint_quality(sprint)
    assert result["has_overlap"] is True
    assert any("no matched existing files" in r.lower() for r in result["reasons"])


# ── 8. Appended/auto-expanded roadmap bucket requires decomposition ────────

def test_auto_expanded_placeholder_requires_decomposition():
    sprint = make_sprint(
        title="Add Candidate Portal / Experience",
        goal="Deliver the Candidate Portal capabilities requested in the feature request. "
             "This sprint was appended because the request was large/multi-role.",
        auto_expanded=True,
    )
    result = p.evaluate_sprint_quality(sprint)
    assert result["requires_decomposition"] is True
    assert result["build_ready"] is False
    assert any("auto-expanded" in r.lower() for r in result["reasons"])


def test_old_sprint_without_auto_expanded_field_does_not_crash():
    """Backward compatibility: a sprint dict from before this feature existed
    (no auto_expanded key at all) must not crash the evaluator."""
    sprint = {"sprint_number": 1, "title": "Add Saved Filters", "goal": "Save filters."}
    result = p.evaluate_sprint_quality(sprint)
    assert isinstance(result["quality_score"], int)
    assert result["sprint_id"] == "sprint_1"


# ── 9 & 10. Artifacts are written ───────────────────────────────────────────

def test_sprint_quality_gate_artifacts_written():
    plan_json = p.normalize_feature_sprint_plan({"sprints": [
        make_sprint(1, title="Requisition Management Dashboard",
                    goal="Add a dashboard page for requisitions.",
                    likely_files_created=["src/components/dashboard.tsx"],
                    completion_criteria=["Dashboard renders open requisitions."],
                    non_goals=["No backend changes."], smoke_checks=["npm run build"]),
        make_sprint(2, title="Backend API and Database Schema",
                    goal="Add backend API and database schema for orders."),
    ]}, "existing app summary")
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run"
        gate = p.write_sprint_quality_gate_artifacts(plan_json, run_dir)
        for fname in ("sprint_quality_gate.md", "sprint_quality_gate.json",
                      "build_ready_sprints.md", "decomposition_needed_sprints.md"):
            assert (run_dir / fname).exists(), fname
        loaded = json.loads((run_dir / "sprint_quality_gate.json").read_text())
        assert loaded["summary"]["total_sprints"] == 2
        assert loaded["summary"]["build_ready_count"] >= 1
        assert loaded["summary"]["requires_decomposition_count"] >= 1

        md = (run_dir / "sprint_quality_gate.md").read_text()
        assert "# Sprint Quality Gate" in md
        assert "Build-ready sprints:" in md
        assert "Needs decomposition:" in md

        ready_md = (run_dir / "build_ready_sprints.md").read_text()
        assert "Requisition Management Dashboard" in ready_md

        decomp_md = (run_dir / "decomposition_needed_sprints.md").read_text()
        assert "Backend API and Database Schema" in decomp_md

        # plan_json itself is mirrored with per-sprint quality + summary.
        assert plan_json["sprints"][0]["quality"]["build_ready"] is True
        assert plan_json["sprints"][1]["quality"]["requires_decomposition"] is True
        assert plan_json["sprint_quality_summary"]["total_sprints"] == 2
        assert gate["summary"]["total_sprints"] == 2


# ── 11. select_feature_sprint blocks non-build-ready sprints (backend guard) ─

def test_select_feature_sprint_blocks_non_build_ready_sprint():
    plan_json = {"sprints": [
        {"sprint_number": 1, "title": "Backend API and Database Schema",
         "quality": {"build_ready": False, "disabled_reason": "Needs decomposition before build",
                     "recommended_next_action": "Decompose before build."}},
    ]}
    try:
        p.select_feature_sprint(plan_json, 1)
        assert False, "expected SprintNotBuildReadyError"
    except p.SprintNotBuildReadyError as e:
        assert "not build-ready" in str(e)
        assert "Needs decomposition before build" in str(e)


def test_select_feature_sprint_allows_build_ready_sprint():
    plan_json = {"sprints": [
        {"sprint_number": 1, "title": "Dashboard", "quality": {"build_ready": True}},
    ]}
    sprint = p.select_feature_sprint(plan_json, 1)
    assert sprint["sprint_number"] == 1


def test_select_feature_sprint_enforce_quality_gate_false_bypasses_guard():
    plan_json = {"sprints": [
        {"sprint_number": 1, "title": "Backend API", "quality": {"build_ready": False}},
    ]}
    sprint = p.select_feature_sprint(plan_json, 1, enforce_quality_gate=False)
    assert sprint["sprint_number"] == 1


def test_backend_continuation_endpoint_blocks_non_build_ready_sprint():
    original_runs_dir = app_mod.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        runs_dir.mkdir()
        app_mod.RUNS_DIR = runs_dir
        source_run = runs_dir / "run_source"
        source_run.mkdir()
        plan = {"sprints": [
            {"sprint_number": 1, "title": "Backend API and Database Schema",
             "quality": {"build_ready": False, "disabled_reason": "Needs decomposition before build"}},
        ]}
        (source_run / "feature_sprint_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        try:
            client = app_mod.app.test_client()
            response = client.post("/api/runs", json={
                "continue_run": str(source_run),
                "continue_feature_sprint": 1,
                "continue_plan_only": False,
            })
            assert response.status_code == 400
            assert "decomposition" in response.get_data(as_text=True).lower()
        finally:
            app_mod.RUNS_DIR = original_runs_dir


# ── 12. Older runs without quality metadata still work ─────────────────────

def test_load_feature_sprint_quality_returns_none_for_run_without_plan():
    """An older source run with no feature_sprint_plan.json at all (pre-quality-
    gate run) must never be blocked by the new guard — load_feature_sprint_quality
    returns None, and the endpoint guard only blocks when it gets an explicit
    build_ready: False back."""
    with tempfile.TemporaryDirectory() as td:
        source_run = Path(td) / "run_source_old"
        source_run.mkdir()
        quality = app_mod.load_feature_sprint_quality(str(source_run), 1)
        assert quality is None


def test_load_feature_sprint_quality_returns_none_for_plan_without_quality_field():
    """A feature_sprint_plan.json that predates the quality gate (no "quality" key
    on the sprint) must also resolve to None, not crash or false-block."""
    with tempfile.TemporaryDirectory() as td:
        source_run = Path(td) / "run_source_old"
        source_run.mkdir()
        (source_run / "feature_sprint_plan.json").write_text(json.dumps({
            "sprints": [{"sprint_number": 1, "title": "Add Saved Filters"}],
        }), encoding="utf-8")
        quality = app_mod.load_feature_sprint_quality(str(source_run), 1)
        assert quality is None


def test_select_feature_sprint_without_quality_field_does_not_block():
    plan_json = {"sprints": [{"sprint_number": 1, "title": "Old Sprint"}]}
    sprint = p.select_feature_sprint(plan_json, 1)
    assert sprint["sprint_number"] == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
