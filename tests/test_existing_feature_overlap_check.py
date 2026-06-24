"""Coverage for the Existing Feature Overlap Check: classifying requested requirement areas
against real existing evidence (including feature module folders the narrower feature gap
matrix scan used to miss), and making the sprint planner overlap-aware. No GPT calls except
in the one end-to-end test, which mocks gpt4o."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


def build_onehr_with_oneats_module(root: Path) -> Path:
    """A OneHR-shaped monorepo with a pre-existing OneATS reporting module living in a
    dedicated module folder (not the generic src/components catch-all) — the exact shape
    that the old feature gap matrix evidence search missed."""
    app = root / "OneHR"
    (app / "OneHR-UI/src/modules/oneats/api").mkdir(parents=True)
    (app / "OneHR-UI/src/modules/oneats/context").mkdir(parents=True)
    (app / "OneHR-UI/src/components").mkdir(parents=True)
    (app / "OneHR-UI/src/pages").mkdir(parents=True)
    (app / "OneHR-UI/tests").mkdir(parents=True)
    (app / "OneHR-API-Backend").mkdir(parents=True)

    (app / "OneHR-UI/package.json").write_text(json.dumps({
        "scripts": {"dev": "vite", "build": "vite build"},
        "dependencies": {"react": "1", "vite": "1"},
        "devDependencies": {"typescript": "1"},
    }))
    (app / "OneHR-UI/vite.config.ts").write_text("export default {}")
    (app / "OneHR-UI/src/App.tsx").write_text("export default function App(){return null}")
    (app / "OneHR-UI/src/main.tsx").write_text("import App from './App'")

    (app / "OneHR-UI/src/modules/oneats/ATSReports.tsx").write_text(
        "export default function ATSReports() {\n"
        "  const totalCandidates = useATSStore(s => s.totalCandidates);\n"
        "  const hired = useATSStore(s => s.hired);\n"
        "  const offers = useATSStore(s => s.offers);\n"
        "  const placements = useATSStore(s => s.placements);\n"
        "  // renders a source chart of candidates by channel\n"
        "  return <div>{totalCandidates} {hired} {offers} {placements}</div>;\n"
        "}\n"
    )
    (app / "OneHR-UI/src/modules/oneats/ATSCandidates.tsx").write_text(
        "export default function ATSCandidates() { return null }\n"
    )
    (app / "OneHR-UI/src/modules/oneats/ATSDocuments.tsx").write_text(
        "export default function ATSDocuments() { return null }\n"
    )
    (app / "OneHR-UI/src/modules/oneats/ATSSettings.tsx").write_text(
        "export default function ATSSettings() { return null }\n"
    )
    (app / "OneHR-UI/src/modules/oneats/context/ATSStore.ts").write_text(
        "export const useATSStore = create(() => ({ totalCandidates: 0, hired: 0, offers: 0, placements: 0 }));\n"
    )
    (app / "OneHR-UI/src/modules/oneats/api/atsApi.ts").write_text(
        "export const atsApi = { getReports: () => fetch('/api/ats/reports') };\n"
    )
    (app / "OneHR-UI/tests/ats.routes.test.ts").write_text(
        "test('ATS route renders sidebar and reports', () => { /* render ATS route */ });\n"
    )

    (app / "OneHR-API-Backend/app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get('/api/ats/reports')\ndef reports(): return {}\n"
    )
    (app / "OneHR-API-Backend/requirements.txt").write_text("fastapi\n")
    return app


def _requirements_text() -> str:
    return ("We need an admin dashboard with reporting and analytics on candidates, hired "
            "candidates, offers, and placements, with source charts.")


def test_existing_oneats_reports_classified_as_implemented_not_missing():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_with_oneats_module(Path(td))
        scan = p.scan_existing_app(app)
        overlap = p.classify_existing_feature_overlap(scan, _requirements_text())
        reporting = overlap["Reporting / Analytics"]
        assert reporting["status"] in ("partially_implemented", "already_implemented")
        assert reporting["status"] != "missing"
        assert any("oneats" in f.lower() for f in reporting["matched_files"])
        assert reporting["recommended_action"] in ("skip", "extend existing")


def test_planner_prefers_oneats_module_files_over_generic_components():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_with_oneats_module(Path(td))
        # Add a generic, unrelated-looking components file that ALSO matches the "report"
        # keyword, to verify module files are still ranked first.
        (app / "OneHR-UI/src/components/GenericReportWidget.tsx").write_text(
            "export default function GenericReportWidget() { return null }\n"
        )
        scan = p.scan_existing_app(app)
        overlap = p.classify_existing_feature_overlap(scan, _requirements_text())
        matched = overlap["Reporting / Analytics"]["matched_files"]
        module_indexes = [i for i, f in enumerate(matched) if "/modules/" in f.lower()]
        component_indexes = [i for i, f in enumerate(matched) if "/components/" in f.lower()]
        assert module_indexes and component_indexes
        assert min(module_indexes) < min(component_indexes)

        # The feature gap matrix's own evidence search must show the same preference.
        run_dir = Path(td) / "run"
        matrix_md = p.generate_feature_gap_matrix(scan, _requirements_text(), run_dir, overlap)
        row = next(line for line in matrix_md.splitlines() if line.startswith("| Reporting / Analytics"))
        modules_pos = row.lower().find("/modules/")
        components_pos = row.lower().find("/components/")
        assert modules_pos != -1
        assert components_pos == -1 or modules_pos < components_pos


def test_existing_feature_overlap_check_artifacts_generated():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_with_oneats_module(Path(td))
        scan = p.scan_existing_app(app)
        run_dir = Path(td) / "run"
        overlap, md = p.generate_existing_feature_overlap_check(scan, _requirements_text(), run_dir)
        assert (run_dir / "existing_feature_overlap_check.md").exists()
        assert (run_dir / "existing_feature_overlap_check.json").exists()
        saved = json.loads((run_dir / "existing_feature_overlap_check.json").read_text())
        assert "Reporting / Analytics" in saved
        for key in ("status", "matched_files", "matched_routes", "evidence_snippets",
                    "likely_missing_gaps", "recommended_action"):
            assert key in saved["Reporting / Analytics"]
        assert saved["Reporting / Analytics"]["status"] in ("already_implemented", "partially_implemented")
        assert "Reporting / Analytics" in md
        assert "Recommended action" in md


def test_feature_gap_matrix_includes_overlap_status_column():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_with_oneats_module(Path(td))
        scan = p.scan_existing_app(app)
        run_dir = Path(td) / "run"
        overlap, _ = p.generate_existing_feature_overlap_check(scan, _requirements_text(), run_dir)
        matrix_md = p.generate_feature_gap_matrix(scan, _requirements_text(), run_dir, overlap)
        assert "Overlap Status" in matrix_md
        assert "Recommended Action" in matrix_md
        row = next(line for line in matrix_md.splitlines() if line.startswith("| Reporting / Analytics"))
        assert overlap["Reporting / Analytics"]["status"] in row


def test_sprint_titled_create_new_when_overlap_exists_produces_warning():
    overlap_check = {
        "Reporting / Analytics": {
            "status": "already_implemented",
            "matched_files": ["OneHR-UI/src/modules/oneats/ATSReports.tsx"],
            "matched_routes": ["GET /api/ats/reports — OneHR-API-Backend/app.py"],
            "matched_tests": ["OneHR-UI/tests/ats.routes.test.ts"],
            "evidence_snippets": [],
            "likely_missing_gaps": [],
            "recommended_action": "skip",
        },
    }
    plan_json = {
        "sprints": [
            {"sprint_number": 1, "title": "Admin Dashboard with Recruitment Metrics",
             "goal": "Create a new admin dashboard reporting on candidates, hires, and offers."},
        ],
    }
    warnings = p._detect_overlap_violations(plan_json, overlap_check)
    assert warnings
    assert "already_implemented" in warnings[0]
    assert "Reporting / Analytics" in warnings[0]

    plan_json["overlap_warnings"] = warnings
    plan_json["total_sprints"] = 1
    plan_json["baseline"] = {"title": "Baseline Existing App", "status": "complete", "description": ""}
    md = p.render_feature_sprint_plan_markdown(plan_json)
    assert "overlap warning" in md.lower()
    assert "existing_feature_overlap_check.md" in md
    terminal = p.render_feature_sprint_plan_terminal(plan_json, None)
    assert "overlap warning" in terminal.lower()


def test_reworded_sprint_creating_parallel_dashboard_file_still_flagged():
    """Structural signal: a sprint can dodge the literal "create new" phrase regex by
    rewording (e.g. "Performance Dashboard for Recruitment Metrics" instead of "Create a new
    admin dashboard") while still proposing a brand-new file that duplicates an existing,
    matched reporting module. This must still be flagged."""
    overlap_check = {
        "Reporting / Analytics": {
            "status": "partially_implemented",
            "matched_files": ["OneHR-UI/src/modules/oneats/ATSReports.tsx"],
            "matched_routes": [],
            "matched_tests": [],
            "evidence_snippets": [],
            "likely_missing_gaps": [],
            "recommended_action": "extend existing",
        },
    }
    plan_json = {
        "sprints": [
            {"sprint_number": 2, "title": "Performance Dashboard for Recruitment Metrics",
             "goal": "Add a user-visible dashboard summarizing organizational performance metrics.",
             "likely_files_created": ["OneHR-UI/src/components/PerformanceDashboard.tsx"],
             "likely_files_modified": ["OneHR-UI/src/components/dashboard.tsx"]},
        ],
    }
    warnings = p._detect_overlap_violations(plan_json, overlap_check)
    assert warnings
    assert "Reporting / Analytics" in warnings[0]
    assert "partially_implemented" in warnings[0]


def test_sprint_that_extends_matched_file_is_not_flagged():
    """If the sprint actually touches the matched existing file, it's a legitimate extension,
    not a duplicate rebuild — must not be flagged even without explicit enhance/extend wording."""
    overlap_check = {
        "Reporting / Analytics": {
            "status": "partially_implemented",
            "matched_files": ["OneHR-UI/src/modules/oneats/ATSReports.tsx"],
            "matched_routes": [],
            "matched_tests": [],
            "evidence_snippets": [],
            "likely_missing_gaps": [],
            "recommended_action": "extend existing",
        },
    }
    plan_json = {
        "sprints": [
            {"sprint_number": 2, "title": "Dashboard Metrics Update",
             "goal": "Add new reporting metrics to the dashboard.",
             "likely_files_created": [],
             "likely_files_modified": ["OneHR-UI/src/modules/oneats/ATSReports.tsx"]},
        ],
    }
    assert p._detect_overlap_violations(plan_json, overlap_check) == []


def test_enhance_wording_does_not_trigger_overlap_warning():
    """No false positives: a sprint that already uses enhance/extend wording for an
    overlapping area should not be flagged."""
    overlap_check = {
        "Reporting / Analytics": {
            "status": "partially_implemented",
            "matched_files": ["OneHR-UI/src/modules/oneats/ATSReports.tsx"],
            "matched_routes": [],
            "matched_tests": [],
            "evidence_snippets": [],
            "likely_missing_gaps": [],
            "recommended_action": "extend existing",
        },
    }
    plan_json = {
        "sprints": [
            {"sprint_number": 1, "title": "Enhance Existing OneATS Reports/Dashboard Metrics",
             "goal": "Extend the existing reporting module with additional analytics."},
        ],
    }
    assert p._detect_overlap_violations(plan_json, overlap_check) == []


def test_no_overlap_warning_when_area_is_missing():
    overlap_check = {
        "Interview Scheduling": {
            "status": "missing",
            "matched_files": [], "matched_routes": [], "matched_tests": [],
            "evidence_snippets": [], "likely_missing_gaps": [],
            "recommended_action": "create new",
        },
    }
    plan_json = {
        "sprints": [
            {"sprint_number": 1, "title": "Create a New Interview Scheduling Module",
             "goal": "Build interview scheduling from scratch."},
        ],
    }
    assert p._detect_overlap_violations(plan_json, overlap_check) == []


def test_feature_sprint_plan_end_to_end_is_overlap_aware():
    """End-to-end: a mocked gpt4o that (incorrectly) proposes creating an already-implemented
    feature must produce overlap_warnings and a warning banner in feature_sprint_plan.md."""
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_with_oneats_module(Path(td))
        scan = p.scan_existing_app(app)
        run_dir = Path(td) / "run"
        overlap, overlap_md = p.generate_existing_feature_overlap_check(scan, _requirements_text(), run_dir)

        old_gpt4o = p.gpt4o
        p.gpt4o = lambda *_a, **_k: json.dumps({"sprints": [{
            "sprint_number": 1, "title": "Admin Dashboard with Recruitment Metrics",
            "goal": "Create a new admin dashboard with reporting on candidates, hires, offers, and placements.",
            "features": ["Admin dashboard"], "requirements_covered": ["Admin dashboard reporting"],
            "completion_criteria": ["works"],
        }]})
        try:
            plan_json, plan_md = p.generate_feature_sprint_plan(
                "existing app summary", _requirements_text(), "gap analysis", "additive architecture",
                run_dir, scan=scan, overlap_check=overlap,
                audit_artifacts={"existing_feature_overlap_check": overlap_md},
            )
        finally:
            p.gpt4o = old_gpt4o

        assert plan_json["overlap_warnings"]
        assert "existing_feature_overlap_check.md" in plan_md
        assert "overlap warning" in plan_md.lower()

        # Requirement: a sprint with overlap warnings must never be left as plain "ready".
        flagged_sprint = next(s for s in plan_json["sprints"] if s["sprint_number"] == 1)
        assert flagged_sprint["status"] in ("needs_revision", "blocked_overlap")
        assert flagged_sprint["overlap_warnings"]
        assert flagged_sprint["overlap_matched_files"]


def test_sprint_with_overlap_warnings_is_not_plain_ready():
    """already_implemented overlap must escalate the sprint's own status field, not just add
    a plan-level warning list — this is what gates the UI build button and the build prompt."""
    overlap_check = {
        "Reporting / Analytics": {
            "status": "already_implemented",
            "matched_files": ["OneHR-UI/src/modules/oneats/ATSReports.tsx"],
            "matched_routes": [], "matched_tests": [], "evidence_snippets": [], "likely_missing_gaps": [],
            "recommended_action": "skip",
        },
    }
    plan_json = {
        "sprints": [
            {"sprint_number": 1, "title": "Admin Dashboard with Recruitment Metrics", "status": "ready",
             "goal": "Create a new admin dashboard reporting on candidates, hires, and offers."},
            {"sprint_number": 2, "title": "Add Interview Scheduling", "status": "ready",
             "goal": "Let recruiters create interview slots and candidates select one."},
        ],
    }
    violations = p._overlap_violations_detailed(plan_json, overlap_check)
    p._apply_overlap_status_to_sprints(plan_json["sprints"], violations)

    flagged = plan_json["sprints"][0]
    clean = plan_json["sprints"][1]
    assert flagged["status"] != "ready"
    assert flagged["status"] in ("needs_revision", "blocked_overlap")
    assert flagged["overlap_warnings"]
    assert "OneHR-UI/src/modules/oneats/ATSReports.tsx" in flagged["overlap_matched_files"]

    # Clean, non-overlapping sprint must still show as plain ready/buildable.
    assert clean["status"] == "ready"
    assert clean["overlap_warnings"] == []
    assert clean["overlap_matched_files"] == []


def test_already_implemented_maps_to_blocked_overlap_partial_to_needs_revision():
    base_sprint = {"sprint_number": 1, "title": "Create a new admin dashboard",
                   "goal": "Create a new admin dashboard for recruitment metrics.", "status": "ready"}

    blocked_check = {"Admin Dashboard": {
        "status": "already_implemented", "matched_files": ["x.tsx"],
        "matched_routes": [], "matched_tests": [], "evidence_snippets": [], "likely_missing_gaps": [],
        "recommended_action": "skip",
    }}
    plan = {"sprints": [dict(base_sprint)]}
    p._apply_overlap_status_to_sprints(plan["sprints"], p._overlap_violations_detailed(plan, blocked_check))
    assert plan["sprints"][0]["status"] == "blocked_overlap"

    partial_check = {"Admin Dashboard": {
        "status": "partially_implemented", "matched_files": ["x.tsx"],
        "matched_routes": [], "matched_tests": [], "evidence_snippets": [], "likely_missing_gaps": [],
        "recommended_action": "extend existing",
    }}
    plan2 = {"sprints": [dict(base_sprint)]}
    p._apply_overlap_status_to_sprints(plan2["sprints"], p._overlap_violations_detailed(plan2, partial_check))
    assert plan2["sprints"][0]["status"] == "needs_revision"


def test_terminal_output_shows_overlap_status_suffix():
    plan_json = {
        "total_sprints": 1,
        "sprints": [{"sprint_number": 1, "title": "Enhance Existing OneATS Dashboard",
                     "goal": "Extend reporting.", "status": "needs_revision",
                     "overlap_warnings": ["..."], "overlap_matched_files": ["x.tsx"]}],
    }
    terminal = p.render_feature_sprint_plan_terminal(plan_json, None)
    assert "Sprint 1 of 1: Enhance Existing OneATS Dashboard — status: needs_revision due to overlap" in terminal


def test_markdown_includes_matched_files_for_overlap_sprint():
    plan_json = {
        "total_sprints": 1,
        "baseline": {"title": "Baseline Existing App", "status": "complete", "description": ""},
        "sprints": [{
            "sprint_number": 1, "title": "Admin Dashboard with Recruitment Metrics",
            "goal": "Create a new admin dashboard.", "status": "blocked_overlap",
            "overlap_warnings": ["Sprint 1 ('Admin Dashboard with Recruitment Metrics') proposes work "
                                  "on Admin Dashboard but existing_feature_overlap_check.md classifies "
                                  "it as already_implemented..."],
            "overlap_matched_files": ["OneHR-UI/src/modules/oneats/ATSAdmin.tsx",
                                       "OneHR-UI/src/components/admin-role-management.tsx"],
        }],
    }
    md = p.render_feature_sprint_plan_markdown(plan_json)
    assert ("Overlap warning: this feature appears partially/already implemented. Extend matched "
            "existing files instead of creating duplicate files.") in md
    assert "OneHR-UI/src/modules/oneats/ATSAdmin.tsx" in md
    assert "OneHR-UI/src/components/admin-role-management.tsx" in md
    assert "**Status:** blocked_overlap" in md


def test_frontend_ui_contract_hides_build_button_for_overlap_sprints():
    """The frontend gates the Build Feature Sprint button purely off plan_json["sprints"][i]
    .status (in {"needs_revision","blocked_overlap"}) and shows overlap_matched_files. This
    test locks the backend contract the UI depends on (no frontend test runner in this repo;
    the TSX/CSS gating itself is verified by `npm run build` type-checking cleanly)."""
    overlap_check = {
        "Reporting / Analytics": {
            "status": "already_implemented", "matched_files": ["a.tsx"],
            "matched_routes": [], "matched_tests": [], "evidence_snippets": [], "likely_missing_gaps": [],
            "recommended_action": "skip",
        },
    }
    plan_json = {"sprints": [
        {"sprint_number": 1, "title": "Create a new reporting dashboard",
         "goal": "Create a new reporting dashboard for metrics.", "status": "ready"},
        {"sprint_number": 2, "title": "Add Interview Scheduling", "status": "ready",
         "goal": "Let recruiters create interview slots."},
    ]}
    p._apply_overlap_status_to_sprints(plan_json["sprints"], p._overlap_violations_detailed(plan_json, overlap_check))

    OVERLAP_BLOCKING_STATUSES = {"needs_revision", "blocked_overlap"}
    overlap_sprint, clean_sprint = plan_json["sprints"]
    assert overlap_sprint["status"] in OVERLAP_BLOCKING_STATUSES  # UI must disable/hide build button
    assert isinstance(overlap_sprint["overlap_matched_files"], list) and overlap_sprint["overlap_matched_files"]
    assert clean_sprint["status"] == "ready"  # UI shows a normal active build button
    assert clean_sprint["overlap_warnings"] == []


def test_build_prompt_for_overlap_sprint_includes_extend_guardrails():
    selected_sprint = {
        "sprint_number": 1, "title": "Admin Dashboard with Recruitment Metrics",
        "goal": "Create a new admin dashboard.", "status": "needs_revision",
        "overlap_warnings": ["Sprint 1 proposes work on Reporting / Analytics but "
                              "existing_feature_overlap_check.md classifies it as "
                              "partially_implemented..."],
        "overlap_matched_files": ["OneHR-UI/src/modules/oneats/ATSReports.tsx"],
        "features": ["Admin dashboard"], "completion_criteria": ["works"],
    }
    plan_json = {"total_sprints": 1, "sprints": [selected_sprint]}
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run"
        prompt = p.generate_selected_feature_sprint_build_prompt(
            "existing app summary", {"tech_stack": ["React"]}, plan_json, selected_sprint, run_dir,
        )
    assert "EXISTING FEATURE OVERLAP" in prompt
    assert "Inspect the matched existing files" in prompt
    assert "OneHR-UI/src/modules/oneats/ATSReports.tsx" in prompt
    assert "do not create a duplicate dashboard, reports, candidate, or recruiter module" in prompt.lower()


def test_build_prompt_for_blocked_overlap_sprint_is_marked_unsafe():
    selected_sprint = {
        "sprint_number": 1, "title": "Admin Dashboard with Recruitment Metrics",
        "goal": "Create a new admin dashboard.", "status": "blocked_overlap",
        "overlap_warnings": ["Sprint 1 proposes work on Admin Dashboard but "
                              "existing_feature_overlap_check.md classifies it as "
                              "already_implemented..."],
        "overlap_matched_files": ["OneHR-UI/src/modules/oneats/ATSAdmin.tsx"],
        "features": ["Admin dashboard"], "completion_criteria": ["works"],
    }
    plan_json = {"total_sprints": 1, "sprints": [selected_sprint]}
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run"
        prompt = p.generate_selected_feature_sprint_build_prompt(
            "existing app summary", {"tech_stack": ["React"]}, plan_json, selected_sprint, run_dir,
        )
    assert "BLOCKED_OVERLAP" in prompt
    assert "ROADMAP REVISION REQUIRED" in prompt
    assert "Do NOT proceed with a from-scratch build" in prompt


def test_build_prompt_for_clean_sprint_has_no_overlap_section():
    selected_sprint = {
        "sprint_number": 1, "title": "Add Interview Scheduling", "status": "ready",
        "goal": "Let recruiters create interview slots.",
        "features": ["Recruiter slot creation"], "completion_criteria": ["works"],
    }
    plan_json = {"total_sprints": 1, "sprints": [selected_sprint]}
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run"
        prompt = p.generate_selected_feature_sprint_build_prompt(
            "existing app summary", {"tech_stack": ["React"]}, plan_json, selected_sprint, run_dir,
        )
    assert "EXISTING FEATURE OVERLAP" not in prompt
    assert "BLOCKED_OVERLAP" not in prompt


if __name__ == "__main__":
    test_existing_oneats_reports_classified_as_implemented_not_missing()
    test_planner_prefers_oneats_module_files_over_generic_components()
    test_existing_feature_overlap_check_artifacts_generated()
    test_feature_gap_matrix_includes_overlap_status_column()
    test_sprint_titled_create_new_when_overlap_exists_produces_warning()
    test_reworded_sprint_creating_parallel_dashboard_file_still_flagged()
    test_sprint_that_extends_matched_file_is_not_flagged()
    test_enhance_wording_does_not_trigger_overlap_warning()
    test_no_overlap_warning_when_area_is_missing()
    test_feature_sprint_plan_end_to_end_is_overlap_aware()
    test_sprint_with_overlap_warnings_is_not_plain_ready()
    test_already_implemented_maps_to_blocked_overlap_partial_to_needs_revision()
    test_terminal_output_shows_overlap_status_suffix()
    test_markdown_includes_matched_files_for_overlap_sprint()
    test_frontend_ui_contract_hides_build_button_for_overlap_sprints()
    test_build_prompt_for_overlap_sprint_includes_extend_guardrails()
    test_build_prompt_for_blocked_overlap_sprint_is_marked_unsafe()
    test_build_prompt_for_clean_sprint_has_no_overlap_section()
    print("PASS: Existing Feature Overlap Check tests")
