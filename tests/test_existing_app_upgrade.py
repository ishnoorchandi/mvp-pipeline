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


def test_saved_filters_entity_fragmentation_is_merged():
    """The reported real-world failure: a mock/local saved-filters feature fragmented into
    one config sprint per entity plus a trailing, separate presets sprint. None of these
    reference a real split boundary (API/backend/schema/role/auth/etc.), so they must merge
    into a small cohesive plan whose first sprint already includes preset save/apply, not
    just raw filter configuration."""
    fragmented_model_plan = {
        "reason_for_split": "Implement filters per page, then presets.",
        "sprints": [
            {
                "sprint_number": 1, "title": "Add Filter Configuration for Requisitions",
                "goal": "Let users configure filters on the Requisitions page.",
                "features": ["Filter configuration UI for Requisitions"],
                "requirements_covered": ["Configure filters on Requisitions"],
                "likely_files_modified": ["frontend/src/pages/Requisitions.tsx"],
                "completion_criteria": ["Requisitions filters can be configured"],
            },
            {
                "sprint_number": 2, "title": "Add Filter Configuration for Candidates",
                "goal": "Let users configure filters on the Candidates page.",
                "depends_on": [0, 1],
                "features": ["Filter configuration UI for Candidates"],
                "requirements_covered": ["Configure filters on Candidates"],
                "likely_files_modified": ["frontend/src/pages/Candidates.tsx"],
                "completion_criteria": ["Candidates filters can be configured"],
            },
            {
                "sprint_number": 3, "title": "Implement Filter Presets Functionality",
                "goal": "Let users save, view, apply, and delete named filter presets.",
                "depends_on": [0, 1, 2],
                "user_visible_result": "Users can save and reapply a named filter preset.",
                "features": ["Save named preset", "Apply preset", "Delete preset"],
                "requirements_covered": ["Save named filter presets", "Apply a saved preset"],
                "likely_files_created": ["frontend/src/components/FilterPresets.tsx"],
                "completion_criteria": ["A saved preset can be applied and deleted"],
            },
        ],
    }
    plan = p.normalize_feature_sprint_plan(fragmented_model_plan, "An existing app with no backend.")
    assert plan["cohesion_repaired"] is True
    assert plan["total_sprints"] in (1, 2)
    combined = " ".join(
        f for s in plan["sprints"] for f in (s["features"] + s["requirements_covered"])
    )
    assert "Requisitions" in combined
    assert "Candidates" in combined
    first_sprint = plan["sprints"][0]
    first_combined = " ".join(first_sprint["features"] + first_sprint["requirements_covered"])
    assert "preset" in first_combined.lower() or "Save named preset" in first_sprint["features"]


def test_interview_scheduling_complex_workflow_stays_multi_sprint():
    """A genuinely complex, multi-role scheduling workflow (role/permission/workflow split
    boundaries present) must NOT be force-merged down to one sprint."""
    plan = p.normalize_feature_sprint_plan({"sprints": [
        {"sprint_number": 1, "title": "Mock Scheduling Service + Recruiter Slot Creation",
         "goal": "Add a mock scheduling service and let recruiters create interview slots.",
         "features": ["Recruiter slot creation", "Mock scheduling service/local state"],
         "completion_criteria": ["Recruiter can create a slot and see it listed"]},
        {"sprint_number": 2, "title": "Candidate Slot Selection View", "depends_on": [0, 1],
         "goal": "Let candidates view and select an available slot (role-specific view).",
         "features": ["Candidate role view", "Slot selection", "Booked status"],
         "completion_criteria": ["Candidate can book an available slot"]},
        {"sprint_number": 3, "title": "Admin Scheduled Interview View + Double-Booking Prevention",
         "depends_on": [0, 1, 2],
         "goal": "Give admins a role-specific view of all interviews and prevent double-booking.",
         "features": ["Admin role view", "Double-booking prevention", "Completed/canceled statuses"],
         "completion_criteria": ["Admin sees all interviews; booking a taken slot is rejected"]},
        {"sprint_number": 4, "title": "Audit Log + Notification Placeholders", "depends_on": [0, 1, 2, 3],
         "goal": "Record audit log entries for scheduling actions and add notification placeholders.",
         "features": ["Audit log entries", "Email notification placeholders"],
         "completion_criteria": ["Booking/canceling writes an audit log entry"]},
    ]}, "An existing frontend-only recruiting dashboard with no backend.")
    assert plan["cohesion_repaired"] is False
    assert 3 <= plan["total_sprints"] <= 6


def test_frontend_only_smoke_checks_avoid_backend_wording():
    plan = p.normalize_feature_sprint_plan(
        {"sprints": [{
            "sprint_number": 1, "title": "Add Saved Filters",
            "goal": "Save filters", "features": ["save"],
            "completion_criteria": ["works"],
            "smoke_checks": ["npm run build", "Query the database to confirm the preset row was inserted"],
            "manual_qa_checklist": ["Hit the real API endpoint and confirm a 200 response"],
        }]},
        "An existing frontend-only app with no backend.",
        scan={"backend_framework": None, "database": None},
    )
    sprint = plan["sprints"][0]
    combined = " ".join(sprint["smoke_checks"] + sprint["manual_qa_checklist"]).lower()
    assert "database" not in combined
    assert "api endpoint" not in combined
    assert "npm run build" in sprint["smoke_checks"]


def test_feature_sprint_plan_prompt_covers_decomposition_requirements():
    prompt = p.FEATURE_SPRINT_PLAN_SYSTEM
    for phrase in (
        "Small UI feature", "Medium mock/frontend-only feature", "Complex workflow feature",
        "deferred_requirements", "mock service/module or local/component state",
        "Never split the SAME capability into multiple sprints",
        "NEVER appear in both", "deferred_requirements\" must be \"[]\"",
    ):
        assert phrase in prompt


def test_saved_filters_single_sprint_does_not_defer_its_own_coverage():
    """Reported bug: a 1-sprint saved-filters plan listed save/view/apply/delete preset
    requirements under both requirements_covered AND deferred_requirements (with a
    "will be covered in next sprint" reason), even though there is no next sprint."""
    plan = p.normalize_feature_sprint_plan({"sprints": [{
        "sprint_number": 1, "title": "Saved Filters for Requisitions and Candidates",
        "goal": "Let users configure, save, view, apply, and delete named filter presets.",
        "user_visible_result": "Users can save and reapply named filter presets.",
        "features": ["Configure filters", "Save named preset", "View saved presets",
                     "Apply saved preset", "Delete saved preset"],
        "requirements_covered": [
            "Save named filter presets", "Display saved presets",
            "Apply saved filter presets", "Delete saved filter preset",
        ],
        "deferred_requirements": [
            "Save named filter presets: will be covered in next sprint",
            "Display saved presets: will be covered in next sprint",
            "Apply saved filter presets: will be covered in next sprint",
            "Delete saved filter preset: will be covered in next sprint",
        ],
        "completion_criteria": ["A saved preset can be applied and deleted"],
    }]}, "An existing app with no backend.")
    assert 1 <= plan["total_sprints"] <= 2
    sprint = plan["sprints"][0]
    assert sprint["deferred_requirements"] == []
    covered_norm = {p._semantic_text(c) for c in sprint["requirements_covered"]}
    deferred_norm = {p._semantic_text(d) for d in sprint["deferred_requirements"]}
    assert not (covered_norm & deferred_norm)
    for item in sprint["deferred_requirements"]:
        assert "next sprint" not in item.lower()


def test_single_sprint_plan_keeps_explicit_out_of_scope_deferral():
    """A single-sprint plan CAN keep a deferred requirement if it is genuinely out of scope
    per the sprint's own non_goals — that is not a contradiction."""
    plan = p.normalize_feature_sprint_plan({"sprints": [{
        "sprint_number": 1, "title": "Add Saved Filters",
        "goal": "Let users save and apply named filter presets.",
        "features": ["Save named preset", "Apply preset"],
        "requirements_covered": ["Save named filter presets", "Apply saved filter presets"],
        "non_goals": ["Sharing presets between different user accounts is out of scope"],
        "deferred_requirements": [
            "Share presets between user accounts: out of scope for this request",
        ],
        "completion_criteria": ["A saved preset can be applied"],
    }]}, "An existing app with no backend.")
    sprint = plan["sprints"][0]
    assert any("share presets" in d.lower() for d in sprint["deferred_requirements"])


def test_last_sprint_never_references_a_nonexistent_next_sprint():
    plan = p.normalize_feature_sprint_plan({"sprints": [
        {"sprint_number": 1, "title": "Add Reports API", "goal": "Add backend report API and schema.",
         "features": ["Report API"], "completion_criteria": ["API returns report data"],
         "non_goals": ["Exporting reports to PDF is out of scope"],
         "deferred_requirements": ["Export reports to PDF: will be covered in next sprint"]},
        {"sprint_number": 2, "title": "Add Reports Page", "goal": "Add report page, nav, and cards.",
         "features": ["Reports page", "Sidebar link", "Report cards"], "depends_on": [0, 1],
         "completion_criteria": ["Page renders API report data"]},
    ]}, "Existing dashboard")
    last_sprint = plan["sprints"][-1]
    for item in last_sprint["deferred_requirements"]:
        assert "next sprint" not in item.lower()


def test_express_wording_corrected_for_react_frontend():
    """Reported bug: sloppy "Express app"/"Express route" wording leaking into plans for a
    scanned React/Create React App frontend that has no Express backend at all."""
    plan = p.normalize_feature_sprint_plan(
        {"sprints": [{
            "sprint_number": 1, "title": "Add Saved Filters",
            "goal": "Register a new Express route for the saved filters page.",
            "features": ["Add an Express app entry for the new page"],
            "completion_criteria": ["The Express route renders the page"],
        }]},
        "An existing React (Create React App) frontend with no backend.",
        scan={"frontend_framework": "React (Create React App)", "backend_framework": None},
    )
    sprint = plan["sprints"][0]
    combined = " ".join([sprint["goal"]] + sprint["features"] + sprint["completion_criteria"])
    assert "express" not in combined.lower()
    assert "React route" in combined or "React app" in combined


def test_express_wording_preserved_when_backend_really_is_express():
    plan = p.normalize_feature_sprint_plan(
        {"sprints": [{
            "sprint_number": 1, "title": "Add Reports API",
            "goal": "Register a new Express route for the reports endpoint.",
            "features": ["Add Express route"], "completion_criteria": ["The Express route responds"],
        }]},
        "An existing Express/Node backend.",
        scan={"frontend_framework": None, "backend_framework": "Express / Node"},
    )
    sprint = plan["sprints"][0]
    combined = " ".join([sprint["goal"]] + sprint["features"])
    assert "express" in combined.lower()


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
                         "post_build_file_snapshot.json", "smoke_mutation_report.md", "smoke_mutation_report.json",
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
    test_saved_filters_entity_fragmentation_is_merged()
    test_interview_scheduling_complex_workflow_stays_multi_sprint()
    test_frontend_only_smoke_checks_avoid_backend_wording()
    test_feature_sprint_plan_prompt_covers_decomposition_requirements()
    test_saved_filters_single_sprint_does_not_defer_its_own_coverage()
    test_single_sprint_plan_keeps_explicit_out_of_scope_deferral()
    test_last_sprint_never_references_a_nonexistent_next_sprint()
    test_express_wording_corrected_for_react_frontend()
    test_express_wording_preserved_when_backend_really_is_express()
    test_changed_files_report_flags_unexpected_deletion()
    test_regression_warns_when_behavior_was_not_exercised()
    test_plan_only_never_runs_claude()
    test_build_mode_creates_accountability_artifacts()
    print("PASS: Existing App Upgrade quality tests")
