"""Coverage for the Existing App Deep Audit upgrade: monorepo-aware scanning, stack
confidence, deep-audit artifacts, the feature gap matrix, and the large multi-role
sprint-plan expansion safety net. No GPT calls — everything here is deterministic."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


def build_onehr_like_monorepo(root: Path) -> Path:
    """A OneHR-shaped monorepo: a React+Vite+TS frontend in OneHR-UI/ and a Flask
    backend in OneHR-API-Backend/, neither at a conventional root/frontend/backend
    path — this is exactly the shape that broke the old fixed-path scanner."""
    app = root / "OneHR"
    (app / "OneHR-UI/src/pages").mkdir(parents=True)
    (app / "OneHR-UI/src/modules/oneats/api").mkdir(parents=True)
    (app / "OneHR-API-Backend/routes").mkdir(parents=True)
    (app / "OneHR-API-Backend/migrations").mkdir(parents=True)
    (app / "OneHR-UI/package.json").write_text(json.dumps({
        "scripts": {"dev": "vite", "build": "vite build", "test": "vitest"},
        "dependencies": {"react": "1", "vite": "1"},
        "devDependencies": {"typescript": "1"},
    }))
    (app / "OneHR-UI/vite.config.ts").write_text("export default {}")
    (app / "OneHR-UI/src/App.tsx").write_text("export default function App(){return null}")
    (app / "OneHR-UI/src/main.tsx").write_text("import App from './App'")
    (app / "OneHR-UI/src/pages/Requisitions.tsx").write_text("export default function R(){return null}")
    (app / "OneHR-UI/src/modules/oneats/api/atsApi.ts").write_text("export const atsApi = fetch")
    (app / "OneHR-API-Backend/app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n"
        "@app.route('/api/jobs')\ndef jobs(): return {}\n"
    )
    (app / "OneHR-API-Backend/requirements.txt").write_text("flask\nPyJWT\n")
    (app / "OneHR-API-Backend/migrations/0001_init.sql").write_text("CREATE TABLE jobs (id int);")
    (app / "OneHR-API-Backend/auth_jwt.py").write_text("import jwt\n")
    # Generated/heavy folders that must be ignored by the scanner.
    (app / "OneHR-UI/node_modules/some-pkg").mkdir(parents=True)
    (app / "OneHR-UI/node_modules/some-pkg/package.json").write_text(json.dumps({
        "dependencies": {"express": "1"},
    }))
    (app / "OneHR-API-Backend/.venv/lib").mkdir(parents=True)
    (app / "OneHR-API-Backend/.venv/lib/requirements.txt").write_text("django\n")
    return app


def test_monorepo_with_frontend_and_backend_folders_is_detected():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        paths = {a["path"] for a in scan["apps"]}
        assert "OneHR-UI" in paths
        assert "OneHR-API-Backend" in paths


def test_package_json_in_child_folder_is_detected():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        frontend_apps = [a for a in scan["apps"] if a["type"] == "frontend"]
        assert any(a["path"] == "OneHR-UI" for a in frontend_apps)
        assert scan["package_manager"] == "npm"


def test_vite_react_typescript_app_in_child_folder_is_detected():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        assert scan["frontend_framework"] == "React + Vite"
        frontend = next(a for a in scan["apps"] if a["path"] == "OneHR-UI")
        assert frontend["language"] == "TypeScript"


def test_python_backend_in_child_folder_is_detected():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        assert scan["backend_framework"] == "Flask"
        backend = next(a for a in scan["apps"] if a["path"] == "OneHR-API-Backend")
        assert backend["language"] == "Python"


def test_sql_migrations_imply_database_evidence():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        assert scan["migrations"]
        assert scan["database"] is not None


def test_jwt_files_imply_auth_evidence():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        assert scan["auth"] == "JWT"


def test_top_level_stack_does_not_say_unknown_when_child_evidence_exists():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        assert "Unknown / undetected" not in scan["tech_stack"]
        assert scan["stack_confidence"] == "high"
        inventory = p.write_existing_app_inventory(scan, Path(td) / "run")
        assert "Unknown / undetected" not in inventory


def test_generated_heavy_folders_are_ignored():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        # The node_modules/express dep and the .venv/django requirements.txt must
        # never leak into detection — only the real app frameworks should show up.
        assert scan["backend_framework"] == "Flask"
        assert "Express / Node" not in scan["tech_stack"]
        assert not any("node_modules" in f for f in scan["all_files"])
        assert not any(".venv" in f for f in scan["all_files"])


def test_child_package_json_scripts_are_detected():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        assert scan["scripts"].get("build") == "vite build"
        assert "OneHR-UI" in scan["scripts_by_app"]
        inventory = p.write_existing_app_inventory(scan, Path(td) / "run")
        assert "no package.json scripts found" not in inventory.lower()
        assert "vite build" in inventory


def test_low_confidence_scan_produces_warnings_not_confident_fake_paths():
    with tempfile.TemporaryDirectory() as td:
        app = Path(td) / "mystery_app"
        (app / "scripts").mkdir(parents=True)
        (app / "scripts" / "run.sh").write_text("echo hi\n")
        scan = p.scan_existing_app(app)
        assert scan["stack_confidence"] == "low"
        assert scan["tech_stack"] == ["Unknown / undetected"]

        run_dir = Path(td) / "run"
        inventory = p.write_existing_app_inventory(scan, run_dir)
        assert "low confidence" in inventory.lower()

        audit = p.run_existing_app_deep_audit(scan, run_dir)
        assert "low confidence" in audit["app_structure_map"].lower()
        assert "low confidence" in audit["implementation_surface_area"].lower()

        plan = p.normalize_feature_sprint_plan(
            {"sprints": [{"sprint_number": 1, "title": "Add Saved Filters", "goal": "Save filters",
                          "features": ["save"], "completion_criteria": ["works"]}]},
            "Existing app", scan=scan,
        )
        assert plan["stack_confidence"] == "low"
        assert "low confidence" in plan["stack_confidence_warning"].lower()
        md = p.render_feature_sprint_plan_markdown(plan)
        assert "low confidence" in md.lower()


def test_existing_app_summary_grounded_with_low_confidence_warning():
    with tempfile.TemporaryDirectory() as td:
        app = Path(td) / "mystery_app"
        (app / "scripts").mkdir(parents=True)
        (app / "scripts" / "run.sh").write_text("echo hi\n")
        scan = p.scan_existing_app(app)
        run_dir = Path(td) / "run"
        old_gpt = p.gpt
        p.gpt = lambda *_a, **_k: "# Existing App Summary\nSome generic prose.\n"
        try:
            summary = p.generate_existing_app_summary(
                "inventory", "health", run_dir, scan=scan, audit_artifacts={},
            )
        finally:
            p.gpt = old_gpt
        assert "stack confidence: low" in summary.lower()
        assert "warning" in summary.lower()


def test_existing_app_summary_mentions_scan_evidence_counts():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        run_dir = Path(td) / "run"
        old_gpt = p.gpt
        p.gpt = lambda *_a, **_k: "# Existing App Summary\nSome generic prose.\n"
        try:
            summary = p.generate_existing_app_summary(
                "inventory", "health", run_dir, scan=scan, audit_artifacts={},
            )
        finally:
            p.gpt = old_gpt
        assert "evidence grounding" in summary.lower()
        assert "detected apps" in summary.lower()
        assert "OneHR-UI" in summary
        assert "OneHR-API-Backend" in summary


def test_deep_audit_writes_all_required_artifacts():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        run_dir = Path(td) / "run"
        artifacts = p.run_existing_app_deep_audit(scan, run_dir)
        for name in ("app_structure_map", "frontend_route_map", "backend_endpoint_map",
                     "data_model_map", "integration_map", "implementation_surface_area"):
            assert name in artifacts
            assert (run_dir / f"{name}.md").exists()
        assert "OneHR-UI" in artifacts["app_structure_map"]
        assert "OneHR-API-Backend" in artifacts["app_structure_map"]


_LARGE_MULTI_ROLE_REQUEST = (
    "This OneATS combined requirements document covers a very large surface area. "
    + ("Admin users must be able to configure system-wide settings and manage tenants. " * 20)
    + ("Recruiters create and manage job requisitions, screen candidates, and schedule interviews. " * 20)
    + ("Candidates use the candidate portal to apply, upload documents, and track status. " * 20)
    + ("Security and RBAC controls restrict access by role and enforce permission checks. " * 20)
    + ("Integrations with external HR systems require external sync of employee records via webhook. " * 20)
    + ("Approval workflows require manager approval before a requisition is published. " * 20)
    + ("Reporting and analytics dashboards summarize pipeline metrics for leadership. " * 20)
    + ("AI matching ranks candidates against requisitions using a resume matching algorithm. " * 20)
    + ("Document workflows support e-signature and document upload for offer letters. " * 20)
    + ("Audit and compliance logging records every sensitive admin and recruiter action. " * 20)
)


def test_large_multi_role_request_does_not_compress_to_3_generic_sprints():
    assert len(_LARGE_MULTI_ROLE_REQUEST) > 6000
    generic_three_sprint_plan = {
        "sprints": [
            {"sprint_number": 1, "title": "Admin Settings", "goal": "Admin settings.",
             "features": ["Admin settings"], "requirements_covered": ["Admin settings"],
             "completion_criteria": ["works"]},
            {"sprint_number": 2, "title": "Recruiter Requisitions", "goal": "Recruiter requisitions.",
             "features": ["Recruiter requisitions"], "requirements_covered": ["Recruiter requisitions"],
             "depends_on": [0, 1], "completion_criteria": ["works"]},
            {"sprint_number": 3, "title": "Candidate Portal", "goal": "Candidate portal.",
             "features": ["Candidate portal"], "requirements_covered": ["Candidate portal"],
             "depends_on": [0, 1, 2], "completion_criteria": ["works"]},
        ],
    }
    plan = p.normalize_feature_sprint_plan(
        generic_three_sprint_plan, "An existing recruiting app.",
        new_feature_requirements=_LARGE_MULTI_ROLE_REQUEST,
    )
    assert plan["total_sprints"] > 3
    combined = " ".join(
        f for s in plan["sprints"] for f in (s["features"] + s["requirements_covered"])
    ).lower()
    for area_hint in ("security", "integration", "audit", "ai matching", "document"):
        assert area_hint in combined


def test_small_request_is_not_affected_by_large_request_expansion():
    """The expansion safety net must never trigger for ordinary small/medium requests."""
    plan = p.normalize_feature_sprint_plan(
        {"sprints": [{"sprint_number": 1, "title": "Add Saved Filters", "goal": "Save filters",
                      "features": ["save"], "completion_criteria": ["works"]}]},
        "An existing app.", new_feature_requirements="Add saved filters to the requisitions page.",
    )
    assert plan["total_sprints"] == 1


def test_feature_gap_matrix_includes_requirement_areas_and_gap_types():
    with tempfile.TemporaryDirectory() as td:
        app = build_onehr_like_monorepo(Path(td))
        scan = p.scan_existing_app(app)
        run_dir = Path(td) / "run"
        matrix_md = p.generate_feature_gap_matrix(
            scan,
            "Add admin settings, recruiter requisitions, candidate portal, and audit compliance logging.",
            run_dir,
        )
        assert (run_dir / "feature_gap_matrix.md").exists()
        for area in ("Admin", "Recruiter", "Candidate", "Audit / Compliance"):
            assert area in matrix_md
        for status_word in ("implemented", "missing", "partially"):
            assert status_word in matrix_md.lower()
        assert "Suggested Sprint Grouping" in matrix_md


# A OneATS-shaped requirements doc: large, multi-role, and deliberately touching every
# requirement area in the taxonomy (admin, recruiter, candidate, requisitions, AI matching,
# interview scheduling, NDA signature, approvals, placement closure, reporting, security,
# audit, Ceipal, job boards, external sync, notifications, data model).
_ONEATS_STYLE_REQUEST = (
    "This OneATS combined requirements document covers a very large surface area. "
    + ("Admin users configure system-wide settings via the admin dashboard. " * 15)
    + ("Recruiters create and manage job requisitions and screen candidates. " * 15)
    + ("Candidates use the candidate portal / candidate experience to apply and track status. " * 15)
    + ("Recruiters handle candidate submission and resume management for each requisition. " * 15)
    + ("AI matching and scoring rank candidates; AI-powered tiering surfaces top matches. " * 15)
    + ("Interview scheduling lets recruiters and candidates coordinate interview slots. " * 15)
    + ("NDA and document signature tracking via e-signature is required before onboarding. " * 15)
    + ("The approval queue routes offer approvals to hiring managers before closure. " * 15)
    + ("Placement closure workflow finalizes a requisition once an offer is accepted. " * 15)
    + ("Reporting and analytics dashboards summarize recruiter and candidate pipeline metrics. " * 15)
    + ("Security and RBAC enforce permission checks across every admin and recruiter action. " * 15)
    + ("Audit and compliance logging records every sensitive action for audit trail review. " * 15)
    + ("Ceipal integration publishes requisitions automatically to the Ceipal ATS. " * 15)
    + ("Job board syndication distributes postings to external job boards. " * 15)
    + ("External sync keeps employee records synchronized with third-party HR integration. " * 15)
    + ("Notifications send email alerts to recruiters and candidates on status changes. " * 15)
    + ("Data model migrations add new schema for placements, approvals, and audit logs. " * 15)
)


def test_large_oneats_style_requirements_detects_at_least_8_areas():
    assert len(_ONEATS_STYLE_REQUEST) > 6000
    areas = p._detected_requirement_areas(_ONEATS_STYLE_REQUEST)
    assert len(areas) >= 8, f"only detected {areas}"


def _compressed_two_sprint_plan():
    return {
        "sprints": [
            {"sprint_number": 1, "title": "Admin Dashboard for Requisition and Candidate Metrics",
             "goal": "Admin dashboard.", "features": ["Admin dashboard metrics"],
             "requirements_covered": ["Admin dashboard metrics"], "completion_criteria": ["works"]},
            {"sprint_number": 2, "title": "Approval Queue for Placements and Offers",
             "goal": "Approval queue.", "features": ["Approval queue for offers"],
             "requirements_covered": ["Approval queue for offers"], "depends_on": [0, 1],
             "completion_criteria": ["works"]},
        ],
    }


def test_large_oneats_request_with_many_areas_cannot_stay_at_2_3_sprints_without_warn():
    """Reproduces run_079: a large OneATS-style request compressed into 2 sprints (Admin
    Dashboard + Approval Queue). Either the safety net must expand the roadmap, or the
    roadmap coverage check must mark it WARN/FAIL — it must never silently PASS."""
    plan = p.normalize_feature_sprint_plan(
        _compressed_two_sprint_plan(), "An existing ATS app.",
        raw_feature_request_text=_ONEATS_STYLE_REQUEST,
    )
    if plan["total_sprints"] <= 3:
        coverage = p.compute_feature_roadmap_coverage(
            "", plan, raw_feature_request_text=_ONEATS_STYLE_REQUEST,
        )
        assert coverage["result"] in ("WARN", "FAIL")
    else:
        assert plan["total_sprints"] >= 6


def test_expand_plan_for_undercovered_topics_expands_compressed_plan_into_richer_roadmap():
    normalized = p.normalize_feature_sprint_plan(
        _compressed_two_sprint_plan(), "An existing ATS app.",
    )["sprints"]
    expanded = p._expand_plan_for_undercovered_topics(
        normalized, "", raw_feature_request_text=_ONEATS_STYLE_REQUEST,
    )
    assert len(expanded) > len(normalized)
    assert len(expanded) >= 6
    titles = " ".join(s.get("title", "") for s in expanded).lower()
    # Grouped clusters, not one thin placeholder per individual area.
    assert "ceipal" in titles or "job board" in titles
    assert "interview scheduling" in titles
    assert "nda" in titles or "signature" in titles


def test_expand_uses_raw_request_when_condensed_summary_is_small():
    """Root cause of the run_079 failure: the GPT-condensed new_feature_requirements text can
    be tiny even when the original raw request is large/multi-role. The expansion safety net
    must key off the raw text, not the condensed one."""
    small_condensed_summary = "Add admin dashboard and approval queue features."
    plan = p.normalize_feature_sprint_plan(
        _compressed_two_sprint_plan(), "An existing ATS app.",
        new_feature_requirements=small_condensed_summary,
        raw_feature_request_text=_ONEATS_STYLE_REQUEST,
    )
    assert plan["total_sprints"] > 2


def test_feature_roadmap_coverage_check_artifacts_generated():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run"
        plan = p.normalize_feature_sprint_plan(_compressed_two_sprint_plan(), "An existing ATS app.")
        coverage, md = p.generate_feature_roadmap_coverage_check(
            "", plan, run_dir, raw_feature_request_text=_ONEATS_STYLE_REQUEST,
        )
        assert (run_dir / "feature_roadmap_coverage_check.md").exists()
        assert (run_dir / "feature_roadmap_coverage_check.json").exists()
        saved = json.loads((run_dir / "feature_roadmap_coverage_check.json").read_text())
        assert saved["result"] == coverage["result"]
        for key in ("detected_requirement_areas", "covered_areas", "deferred_areas",
                    "uncovered_areas", "suspiciously_compressed", "result"):
            assert key in saved
        assert "Detected Requirement Areas" in md


def test_coverage_check_marks_compressed_plans_as_warn_or_fail():
    plan = p.normalize_feature_sprint_plan(_compressed_two_sprint_plan(), "An existing ATS app.")
    coverage = p.compute_feature_roadmap_coverage("", plan, raw_feature_request_text=_ONEATS_STYLE_REQUEST)
    assert coverage["result"] in ("WARN", "FAIL")
    assert coverage["uncovered_areas"] or coverage["suspiciously_compressed"]

    # A small request that doesn't touch any taxonomy area must still PASS — the check
    # should not cry wolf on ordinary small/medium feature work.
    small_plan = p.normalize_feature_sprint_plan(
        {"sprints": [{"sprint_number": 1, "title": "Add a Dark Mode Toggle", "goal": "Add dark mode.",
                      "features": ["Dark mode toggle"], "completion_criteria": ["works"]}]},
        "An existing app.",
    )
    small_coverage = p.compute_feature_roadmap_coverage(
        "Add a dark mode toggle to the settings page.", small_plan,
    )
    assert small_coverage["result"] == "PASS"


def test_feature_sprint_plan_markdown_includes_warning_when_areas_uncovered():
    plan = p.normalize_feature_sprint_plan(_compressed_two_sprint_plan(), "An existing ATS app.")
    plan["roadmap_coverage_result"] = "WARN"
    plan["roadmap_coverage_uncovered_areas"] = ["Interview Scheduling", "Ceipal Integration"]
    md = p.render_feature_sprint_plan_markdown(plan)
    assert "Roadmap coverage check: WARN" in md
    assert "Interview Scheduling" in md
    terminal = p.render_feature_sprint_plan_terminal(plan, None)
    assert "Roadmap coverage check: WARN" in terminal


if __name__ == "__main__":
    test_monorepo_with_frontend_and_backend_folders_is_detected()
    test_package_json_in_child_folder_is_detected()
    test_vite_react_typescript_app_in_child_folder_is_detected()
    test_python_backend_in_child_folder_is_detected()
    test_sql_migrations_imply_database_evidence()
    test_jwt_files_imply_auth_evidence()
    test_top_level_stack_does_not_say_unknown_when_child_evidence_exists()
    test_generated_heavy_folders_are_ignored()
    test_child_package_json_scripts_are_detected()
    test_low_confidence_scan_produces_warnings_not_confident_fake_paths()
    test_existing_app_summary_grounded_with_low_confidence_warning()
    test_existing_app_summary_mentions_scan_evidence_counts()
    test_deep_audit_writes_all_required_artifacts()
    test_large_multi_role_request_does_not_compress_to_3_generic_sprints()
    test_small_request_is_not_affected_by_large_request_expansion()
    test_feature_gap_matrix_includes_requirement_areas_and_gap_types()
    test_large_oneats_style_requirements_detects_at_least_8_areas()
    test_large_oneats_request_with_many_areas_cannot_stay_at_2_3_sprints_without_warn()
    test_expand_plan_for_undercovered_topics_expands_compressed_plan_into_richer_roadmap()
    test_expand_uses_raw_request_when_condensed_summary_is_small()
    test_feature_roadmap_coverage_check_artifacts_generated()
    test_coverage_check_marks_compressed_plans_as_warn_or_fail()
    test_feature_sprint_plan_markdown_includes_warning_when_areas_uncovered()
    print("PASS: Existing App Deep Audit tests")
