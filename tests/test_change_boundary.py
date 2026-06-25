"""Selected Feature Change Boundary — Existing App Upgrade hardening.

Proves: boundary artifacts are generated, fix prompts carry a hard file
boundary, review findings are classified and filtered before any fix prompt
is built, unrelated deletes/modifies are caught as boundary violations, clean
in-bound changes pass, and Local Delivery is blocked when the boundary fails.

Uses temporary fixture apps only — never touches OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


def fixture_app(root: Path) -> Path:
    app = root / "fixture_app"
    (app / "src/mock").mkdir(parents=True)
    (app / "src/components").mkdir(parents=True)
    (app / "src/utils").mkdir(parents=True)
    (app / "src/lib").mkdir(parents=True)
    (app / "public").mkdir(parents=True)
    (app / "src/mock/demoData.ts").write_text("export const demoData = [];\n")
    (app / "src/components/DemoDashboardCard.tsx").write_text("export default function DemoDashboardCard(){return null}\n")
    (app / "src/components/dashboard.tsx").write_text("export default function Dashboard(){return null}\n")
    (app / "src/utils/config.ts").write_text("export const CONFIG = {};\n")
    (app / "src/lib/mock-data.ts").write_text("export const mockData = [];\n")
    (app / "src/components/figma/ImageWithFallback.tsx").parent.mkdir(parents=True, exist_ok=True)
    (app / "src/components/figma/ImageWithFallback.tsx").write_text("export default function X(){return null}\n")
    return app


def demo_sprint() -> dict:
    """Mirrors the real bug report: a small sprint scoped to 3 files only."""
    return {
        "sprint_number": 1,
        "title": "Add Demo Dashboard Card",
        "goal": "Show a demo dashboard card with mock data on the dashboard.",
        "features": ["DemoDashboardCard component", "Mock demo data"],
        "likely_files_created": ["src/mock/demoData.ts", "src/components/DemoDashboardCard.tsx"],
        "likely_files_modified": ["src/components/dashboard.tsx"],
        "must_not_modify": ["src/utils/config.ts"],
        "expected_deletions": [],
        "completion_criteria": ["Demo dashboard card renders with mock data"],
        "manual_qa_checklist": [],
    }


# ── 1. A selected feature change boundary is generated ─────────────────────

def test_change_boundary_is_generated():
    with tempfile.TemporaryDirectory() as td:
        rdir = Path(td) / "run"
        sprint = demo_sprint()
        boundary = p.generate_selected_feature_change_boundary(
            sprint, {"sprints": [sprint]}, "Additive architecture text", "build prompt text", rdir,
        )
        assert boundary["sprint_number"] == 1
        assert "src/mock/demoData.ts" in boundary["expected_files_create"]
        assert "src/components/dashboard.tsx" in boundary["expected_files_modify"]
        assert "src/components" in boundary["allowed_directories"]
        assert boundary["protected_existing_files"] == ["src/utils/config.ts"]
        assert boundary["forbidden_deletes_default"] is True
        assert (rdir / "selected_feature_change_boundary.json").exists()
        assert (rdir / "selected_feature_change_boundary.md").exists()
        md = (rdir / "selected_feature_change_boundary.md").read_text()
        assert "demoData.ts" in md and "DemoDashboardCard.tsx" in md
        assert "No deletions are expected or allowed" in md


# ── 2. Fix prompt includes exact allowed file boundary ──────────────────────

def test_fix_prompt_includes_hard_file_boundary():
    sprint = demo_sprint()
    boundary = p.generate_selected_feature_change_boundary(
        sprint, {"sprints": [sprint]}, "", "", Path(tempfile.mkdtemp()),
    )
    classification = {"findings": [], "counts": {
        "selected_sprint_actionable": 0, "out_of_scope_existing_app_issue": 0,
        "needs_human_review": 0, "blocked_by_boundary": 0,
    }}
    prompt = p.generate_existing_app_fix_prompt("summary", boundary, classification, 1)
    assert "src/mock/demoData.ts" in prompt
    assert "src/components/DemoDashboardCard.tsx" in prompt
    assert "src/components/dashboard.tsx" in prompt
    assert "src/utils/config.ts" in prompt
    assert "Do not delete existing files unless explicitly listed as expected deletion" in prompt
    assert "Do not modify unrelated existing files" in prompt
    assert "classify it as out of scope and do not change files" in prompt
    assert "Only fix selected-sprint actionable issues" in prompt


# ── 3 & 4. Review findings classified and filtered before the fix prompt ───

def test_out_of_scope_review_findings_are_filtered():
    sprint = demo_sprint()
    boundary = p.generate_selected_feature_change_boundary(
        sprint, {"sprints": [sprint]}, "", "", Path(tempfile.mkdtemp()),
    )
    report = (
        "## ISSUES\n"
        "- New demo card has broken rendering on small screens.\n"
        "- Existing backend has no auth on its routes.\n"
        "- Existing config file has old commented placeholders.\n"
        "- Existing image fallback uses base64.\n"
        "- Request requires changing auth to support the demo card.\n"
    )
    classification = p.classify_review_findings(report, boundary, sprint)
    by_text = {f["text"]: f["category"] for f in classification["findings"]}

    assert by_text["New demo card has broken rendering on small screens."] == "selected_sprint_actionable"
    assert by_text["Existing backend has no auth on its routes."] == "out_of_scope_existing_app_issue"
    assert by_text["Existing config file has old commented placeholders."] == "out_of_scope_existing_app_issue"
    assert by_text["Existing image fallback uses base64."] == "out_of_scope_existing_app_issue"
    assert by_text["Request requires changing auth to support the demo card."] in (
        "needs_human_review", "blocked_by_boundary",
    )
    assert classification["counts"]["selected_sprint_actionable"] == 1
    assert classification["counts"]["out_of_scope_existing_app_issue"] == 3


def test_fix_prompt_excludes_out_of_scope_findings():
    sprint = demo_sprint()
    boundary = p.generate_selected_feature_change_boundary(
        sprint, {"sprints": [sprint]}, "", "", Path(tempfile.mkdtemp()),
    )
    report = (
        "- New demo card has broken rendering.\n"
        "- Existing backend has no auth on its routes.\n"
    )
    classification = p.classify_review_findings(report, boundary, sprint)
    prompt = p.generate_existing_app_fix_prompt("summary", boundary, classification, 1)

    issues_section = prompt.split("## ISSUES TO FIX")[1].split("## OUT-OF-SCOPE FINDINGS")[0]
    assert "broken rendering" in issues_section
    assert "no auth" not in issues_section
    assert "## OUT-OF-SCOPE FINDINGS" in prompt
    assert "no auth on its routes" in prompt.split("## OUT-OF-SCOPE FINDINGS")[1]


# ── 5. Deleting unrelated existing files creates a boundary violation ──────

def test_deleting_unrelated_file_is_a_boundary_violation():
    sprint = demo_sprint()
    boundary = p.generate_selected_feature_change_boundary(
        sprint, {"sprints": [sprint]}, "", "", Path(tempfile.mkdtemp()),
    )
    changed_files = {
        "added": ["src/mock/demoData.ts", "src/components/DemoDashboardCard.tsx"],
        "modified": ["src/components/dashboard.tsx"],
        "deleted": ["src/utils/config.ts", "src/lib/mock-data.ts"],
    }
    result = p.check_selected_feature_boundary(changed_files, boundary)
    assert result["status"] == "FAIL"
    assert "src/utils/config.ts" in result["unauthorized_deletions"]
    assert "src/lib/mock-data.ts" in result["unauthorized_deletions"]
    assert any(v["type"] == "unauthorized_deletion" and v["severity"] == "high" for v in result["violations"])


# ── 6. Modifying unrelated existing files creates a boundary violation ─────

def test_modifying_unrelated_file_is_a_boundary_violation():
    sprint = demo_sprint()
    boundary = p.generate_selected_feature_change_boundary(
        sprint, {"sprints": [sprint]}, "", "", Path(tempfile.mkdtemp()),
    )
    changed_files = {
        "added": ["public/image-fallback.svg"],
        "modified": ["src/components/dashboard.tsx", "src/components/figma/ImageWithFallback.tsx"],
        "deleted": [],
    }
    result = p.check_selected_feature_boundary(changed_files, boundary)
    assert result["status"] == "FAIL"
    assert "public/image-fallback.svg" in result["unexpected_files"]
    assert "src/components/figma/ImageWithFallback.tsx" in result["unexpected_files"]
    assert "src/components/dashboard.tsx" not in result["unexpected_files"]


# ── 7. Local Delivery is blocked if a boundary violation exists ────────────

def test_local_delivery_blocked_when_boundary_failed():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import backend.app as app_mod

    app_mod.RUNS_DIR.mkdir(exist_ok=True)
    run_id = "run_test_boundary_block"
    rdir = app_mod.RUNS_DIR / run_id
    rdir.mkdir(exist_ok=True)
    try:
        (rdir / "run_state.json").write_text(json.dumps({
            "run_id": run_id, "existing_app": "/tmp/does-not-matter",
            "change_boundary_status": "FAIL", "boundary_violation_count": 2,
            "local_delivery_blocked_by_boundary": True,
        }))
        client = app_mod.app.test_client()

        r = client.post(f"/api/runs/{run_id}/delivery/commit",
                         json={"branch_name": "pipeline/test", "commit_message": "msg"})
        assert r.status_code == 409
        assert b"boundary" in r.data.lower()

        r = client.post(f"/api/runs/{run_id}/delivery/push",
                         json={"branch_name": "demo/test", "commit_message": "msg"})
        assert r.status_code == 409

        r = client.get(f"/api/runs/{run_id}/delivery")
        body = r.get_json()
        assert body["boundary"]["blocked"] is True
        assert body["boundary"]["status"] == "FAIL"
    finally:
        import shutil
        shutil.rmtree(rdir, ignore_errors=True)


# ── 8. In-bound new files and in-bound modifications pass ──────────────────

def test_inbound_changes_pass_the_boundary():
    sprint = demo_sprint()
    boundary = p.generate_selected_feature_change_boundary(
        sprint, {"sprints": [sprint]}, "", "", Path(tempfile.mkdtemp()),
    )
    changed_files = {
        "added": ["src/mock/demoData.ts", "src/components/DemoDashboardCard.tsx"],
        "modified": ["src/components/dashboard.tsx"],
        "deleted": [],
    }
    result = p.check_selected_feature_boundary(changed_files, boundary)
    assert result["status"] == "PASS"
    assert result["unexpected_files"] == []
    assert result["unauthorized_deletions"] == []


# ── 9. Existing App Upgrade still works when the build stays inside the boundary ─

def test_regression_check_passes_clean_when_boundary_clean():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        rdir = root / "run"
        rdir.mkdir()
        sprint = demo_sprint()

        p.snapshot_protected_files(app, sprint["must_not_modify"], rdir)
        p.snapshot_existing_files(app, rdir)

        # Build stays exactly inside the declared boundary: only edits dashboard.tsx,
        # only creates the two expected new files.
        (app / "src/components/dashboard.tsx").write_text(
            "import DemoDashboardCard from './DemoDashboardCard';\nexport default function Dashboard(){return null}\n"
        )

        changed_files, changed_report = p.write_changed_files_report(app, rdir, sprint)
        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        boundary_result = p.check_selected_feature_boundary(changed_files, boundary)
        assert boundary_result["status"] == "PASS"

        status, report = p.run_regression_check(
            app, rdir, sprint, smoke_log="", changed_files=changed_files,
            baseline_checklist="", boundary_result=boundary_result,
        )
        assert status != "FAIL"
        assert "Boundary status:** PASS" in report


def test_regression_check_fails_when_boundary_fails():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        rdir = root / "run"
        rdir.mkdir()
        sprint = demo_sprint()

        p.snapshot_protected_files(app, sprint["must_not_modify"], rdir)
        p.snapshot_existing_files(app, rdir)

        # Simulate the real bug: an unrelated existing file gets deleted by a fix pass.
        (app / "src/utils/config.ts").unlink()

        changed_files, _ = p.write_changed_files_report(app, rdir, sprint)
        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        boundary_result = p.check_selected_feature_boundary(changed_files, boundary)
        assert boundary_result["status"] == "FAIL"

        status, report = p.run_regression_check(
            app, rdir, sprint, smoke_log="", changed_files=changed_files,
            baseline_checklist="", boundary_result=boundary_result,
        )
        assert status == "FAIL"
        p.write_boundary_violation_report(boundary_result, boundary, rdir)
        assert (rdir / "boundary_violation_report.md").exists()
        assert "src/utils/config.ts" in (rdir / "boundary_violation_report.md").read_text()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
