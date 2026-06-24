"""Focused deterministic coverage for Existing App Upgrade safety and quality."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


def fixture_app(root: Path) -> Path:
    app = root / "fixture_app"
    (app / "frontend/src/pages").mkdir(parents=True)
    (app / "frontend/src/api").mkdir(parents=True)
    (app / "backend/routes").mkdir(parents=True)
    (app / "tests").mkdir()
    (app / "frontend/package.json").write_text(json.dumps({
        "scripts": {"dev": "vite", "build": "vite build", "test": "vitest"},
        "dependencies": {"react": "1", "vite": "1", "axios": "1"},
    }))
    (app / "frontend/src/pages/Home.tsx").write_text("export default function Home(){return <div/>}")
    (app / "frontend/src/api/client.ts").write_text("export const api = fetch")
    (app / "backend/app.py").write_text(
        "from flask import Flask\napp=Flask(__name__)\n@app.get('/health')\ndef health(): return {}\n"
    )
    (app / "backend/routes/items.py").write_text("@bp.post('/api/items')\ndef add(): return {}\n")
    (app / "requirements.txt").write_text("flask\n")
    (app / "tests/test_health.py").write_text("def test_health(): pass\n")
    return app


def rich_sprint() -> dict:
    return p.normalize_feature_sprint_plan({"sprints": [{
        "sprint_number": 1, "title": "Add Saved Filters to Resource Browser",
        "goal": "Users can save filters.", "user_visible_result": "Saved filters can be restored.",
        "features": ["Save filter"], "requirements_covered": ["Persist named filters"],
        "likely_files_created": ["frontend/src/components/SavedFilters.tsx"],
        "likely_files_modified": ["frontend/src/pages/Home.tsx"],
        "must_not_modify": ["backend/app.py"], "non_goals": ["No auth changes"],
        "regression_risks": ["Existing resource list filtering"],
        "completion_criteria": ["A named filter can be saved and restored"],
        "smoke_checks": ["npm run build"], "manual_qa_checklist": ["Reload a saved filter"],
        "independently_demoable": True,
    }]}, "summary")


def test_deep_scan_and_baseline_checklist():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        scan = p.scan_existing_app(app)
        assert scan["frontend_framework"] == "React + Vite"
        assert any("GET /health" in route for route in scan["backend_routes"])
        assert any("POST /api/items" in route for route in scan["backend_routes"])
        assert scan["test_files"] and scan["api_client_files"]
        health = p.run_baseline_health_check(app, scan, root / "run")
        checklist = p.write_baseline_behavior_checklist(scan, health, root / "run")
        assert "npm run dev" in checklist and "npm run build" in checklist and "npm run test" in checklist
        assert "/health" in checklist and "manual verification required" in checklist


def test_plan_and_build_prompt_have_strict_upgrade_fields():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        plan = rich_sprint()
        sprint = plan["sprints"][0]
        md = p.render_feature_sprint_plan_markdown(plan)
        assert "Exact requirements covered" in md and "Explicit non-goals" in md
        assert "Regression risks" in md and "Manual QA checklist" in md
        prompt = p.generate_selected_feature_sprint_build_prompt(
            "Existing resource browser", p.scan_existing_app(app), plan, sprint, root / "run",
            "# Baseline Behavior Checklist\n- [ ] Existing home page",
        )
        for phrase in ("smallest additive change", "Do not rewrite unrelated files",
                       "Do not rename routes", "Do not remove existing features",
                       "YOU MUST NOT MODIFY", "manual verification required"):
            assert phrase in prompt


def test_simple_reports_page_is_one_complete_vertical_sprint():
    fragmented_model_plan = {
        "reason_for_split": "Implement the page in small steps.",
        "sprints": [
            {
                "sprint_number": 1,
                "title": "Add Reports Route and Empty Page",
                "goal": "Register /reports and render an empty Reports page.",
                "features": ["Reports route/page"],
                "requirements_covered": ["Create a Reports page"],
                "likely_files_created": ["frontend/src/pages/Reports.tsx"],
                "likely_files_modified": ["frontend/src/App.tsx"],
                "regression_risks": ["Existing dashboard routes"],
                "completion_criteria": ["The /reports route renders"],
            },
            {
                "sprint_number": 2,
                "title": "Add Reports Sidebar Link",
                "goal": "Add a navigation link to the Reports page.",
                "features": ["Sidebar/nav link"],
                "requirements_covered": ["Make Reports discoverable from the sidebar"],
                "depends_on": [0, 1],
                "likely_files_modified": ["frontend/src/components/Sidebar.tsx"],
                "completion_criteria": ["The Reports sidebar link navigates to /reports"],
            },
            {
                "sprint_number": 3,
                "title": "Add Mock Report Cards",
                "goal": "Fill the Reports page with useful mock report cards.",
                "user_visible_result": "Users can open Reports and view report cards.",
                "features": ["Mock report cards"],
                "requirements_covered": ["Show mock report cards"],
                "depends_on": [0, 1, 2],
                "likely_files_modified": ["frontend/src/pages/Reports.tsx"],
                "completion_criteria": ["Reports displays the requested cards"],
            },
        ],
    }
    plan = p.normalize_feature_sprint_plan(
        fragmented_model_plan,
        "An existing dashboard with established routes, sidebar navigation, and dashboard behavior.",
    )
    assert plan["cohesion_repaired"] is True
    assert plan["total_sprints"] == 1
    sprint = plan["sprints"][0]
    combined = " ".join(sprint["features"] + sprint["requirements_covered"])
    assert "Reports route/page" in combined
    assert "Sidebar/nav link" in combined
    assert "Mock report cards" in combined
    assert "Preserve existing dashboard behavior" in sprint["regression_risks"]
    assert sprint["depends_on"] == [0]
    assert sprint["independently_demoable"] is True


def test_meaningful_backend_boundary_remains_split():
    plan = p.normalize_feature_sprint_plan({"sprints": [
        {"sprint_number": 1, "title": "Add Reports API", "goal": "Add backend report API and schema.",
         "features": ["Report API"], "completion_criteria": ["API returns report data"]},
        {"sprint_number": 2, "title": "Add Reports Page", "goal": "Add report page, nav, and cards.",
         "features": ["Reports page", "Sidebar link", "Report cards"], "depends_on": [0, 1],
         "completion_criteria": ["Page renders API report data"]},
    ]}, "Existing dashboard")
    assert plan["total_sprints"] == 2
    assert plan["cohesion_repaired"] is False


def test_changed_files_report_flags_unexpected_deletion():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        run = root / "run"
        run.mkdir()
        p.snapshot_existing_files(app, run)
        (app / "backend/app.py").unlink()
        result, report = p.write_changed_files_report(app, run, rich_sprint()["sprints"][0])
        assert "backend/app.py" in result["deleted"]
        assert "backend/app.py" in result["suspicious"]
        assert "high risk" in report


def test_regression_warns_when_behavior_was_not_exercised():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        run = root / "run"
        run.mkdir()
        sprint = rich_sprint()["sprints"][0]
        p.snapshot_protected_files(app, sprint["must_not_modify"], run)
        p.snapshot_existing_files(app, run)
        changes, _ = p.write_changed_files_report(app, run, sprint)
        status, report = p.run_regression_check(app, run, sprint, "", changes, "checklist")
        assert status == "WARN"
        assert "manual verification required" in report
        status, _ = p.run_regression_check(
            app, run, sprint, "SUMMARY\nPASS : 4\nFAIL : 0\nRESULT: ALL CHECKS PASSED", changes, "checklist"
        )
        assert status != "FAIL", "FAIL : 0 must not be parsed as a failed smoke check"


def test_plan_only_never_runs_claude():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        old_runs, old_gpt, old_gpt4o, old_build = p.RUNS_DIR, p.gpt, p.gpt4o, p.build_feature_sprint
        p.RUNS_DIR = root / "runs"
        p.RUNS_DIR.mkdir()
        p.gpt = lambda *_args, **_kwargs: "# Generated\n"
        p.gpt4o = lambda *_args, **_kwargs: json.dumps({"sprints": [{
            "sprint_number": 1, "title": "Add Saved Filters", "goal": "Save filters",
            "features": ["save"], "completion_criteria": ["works"],
        }]})
        called = []
        p.build_feature_sprint = lambda *_args, **_kwargs: called.append(True) or ""
        try:
            run_id = p.pipeline_existing_app_upgrade(str(app), "save filters", feature_plan_only=True, use_deepseek=False)
            run = p.RUNS_DIR / run_id
            assert not called
            assert (run / "feature_sprint_plan.json").exists()
            assert (run / "baseline_behavior_checklist.md").exists()
            assert not (run / "selected_feature_sprint_build_prompt.txt").exists()
        finally:
            p.RUNS_DIR, p.gpt, p.gpt4o, p.build_feature_sprint = old_runs, old_gpt, old_gpt4o, old_build


def test_build_mode_creates_accountability_artifacts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        saved = (p.RUNS_DIR, p.gpt, p.gpt4o, p.build_feature_sprint, p.run_smoke_checks)
        p.RUNS_DIR = root / "runs"
        p.RUNS_DIR.mkdir()
        p.gpt = lambda *_args, **_kwargs: "# Generated\n"
        p.gpt4o = lambda *_args, **_kwargs: json.dumps({"sprints": [{
            "sprint_number": 1, "title": "Add Saved Filters", "goal": "Save filters",
            "features": ["save"], "likely_files_modified": ["frontend/src/pages/Home.tsx"],
            "must_not_modify": ["backend/app.py"], "completion_criteria": ["works"],
            "manual_qa_checklist": ["save and reload"],
        }]})
        def fake_build(*_args, **_kwargs):
            target = app / "frontend/src/pages/Home.tsx"
            target.write_text(target.read_text() + "\n// saved filters\n")
            return "Modified frontend/src/pages/Home.tsx: added saved filters"
        p.build_feature_sprint = fake_build
        p.run_smoke_checks = lambda *_args, **_kwargs: "PASS: npm run build"
        try:
            run_id = p.pipeline_existing_app_upgrade(str(app), "save filters", use_deepseek=False)
            run = p.RUNS_DIR / run_id
            for name in ("selected_feature_sprint_build_prompt.txt", "baseline_file_snapshot.json",
                         "changed_files_report.md", "smoke_test_log.txt", "regression_check.md",
                         "feature_completion_report.md"):
                assert (run / name).exists(), name
        finally:
            (p.RUNS_DIR, p.gpt, p.gpt4o, p.build_feature_sprint, p.run_smoke_checks) = saved


if __name__ == "__main__":
    test_deep_scan_and_baseline_checklist()
    test_plan_and_build_prompt_have_strict_upgrade_fields()
    test_simple_reports_page_is_one_complete_vertical_sprint()
    test_meaningful_backend_boundary_remains_split()
    test_changed_files_report_flags_unexpected_deletion()
    test_regression_warns_when_behavior_was_not_exercised()
    test_plan_only_never_runs_claude()
    test_build_mode_creates_accountability_artifacts()
    print("PASS: Existing App Upgrade quality tests")
