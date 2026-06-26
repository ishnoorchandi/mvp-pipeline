"""Bugfix Mode Foundation tests.

Fixture repos only. Bugfix mode is planning-only: no code changes, commits,
pushes, or PRs are created.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


def test_bug_report_parser_handles_full_report():
    report = """Title: Candidate dashboard does not load
Expected: Dashboard shows candidate rows.
Actual: Page crashes with a console error.
Steps to reproduce:
1. Open /candidates
2. Click Load
Console error: TypeError in CandidateDashboard.tsx at loadCandidates
Backend error: GET /api/candidates returns 500
Affected page: /candidates
Affected endpoint: /api/candidates
Screenshot notes: blank table
"""
    parsed = p.parse_bug_report(report)
    assert parsed["bug_title"] == "Candidate dashboard does not load"
    assert parsed["expected_behavior"] == "Dashboard shows candidate rows."
    assert "Page crashes" in parsed["actual_behavior"]
    assert "/api/candidates" in parsed["api_endpoint_clues"]
    assert "CandidateDashboard.tsx" in parsed["affected_files_mentioned"]
    assert parsed["severity_guess"] == "high"
    assert parsed["likely_category"] == "mixed"


def test_bug_report_parser_handles_missing_sections():
    parsed = p.parse_bug_report("Actual: Submit button fails")
    assert parsed["bug_title"] == "Actual: Submit button fails"
    assert parsed["expected_behavior"] == "unknown"
    assert parsed["actual_behavior"] == "Submit button fails"
    assert parsed["affected_page"] == "unknown"


def test_frontend_error_clue_finds_frontend_candidate_files():
    with tempfile.TemporaryDirectory() as td:
        app = Path(td)
        src = app / "src" / "components"
        src.mkdir(parents=True)
        (src / "CandidateDashboard.tsx").write_text("export function loadCandidates() { throw new Error('boom') }\n")
        parsed = p.parse_bug_report("Console error: TypeError in CandidateDashboard.tsx at loadCandidates")
        investigation = p.investigate_bugfix_repo(app, parsed)
        files = [item["file"] for item in investigation["frontend_candidates"]]
        assert "src/components/CandidateDashboard.tsx" in files


def test_endpoint_clue_finds_backend_or_api_candidate_files():
    with tempfile.TemporaryDirectory() as td:
        app = Path(td)
        api = app / "server"
        api.mkdir()
        (api / "routes.py").write_text("@app.route('/api/candidates')\ndef candidates(): pass\n")
        parsed = p.parse_bug_report("Affected endpoint: /api/candidates\nBackend error: 500")
        investigation = p.investigate_bugfix_repo(app, parsed)
        files = [item["file"] for item in investigation["backend_candidates"] + investigation["api_client_candidates"]]
        assert "server/routes.py" in files


def test_generated_folders_are_skipped_during_search():
    with tempfile.TemporaryDirectory() as td:
        app = Path(td)
        generated = app / "node_modules" / "pkg"
        generated.mkdir(parents=True)
        (generated / "CandidateDashboard.tsx").write_text("loadCandidates\n")
        parsed = p.parse_bug_report("Console error: CandidateDashboard.tsx loadCandidates")
        investigation = p.investigate_bugfix_repo(app, parsed)
        all_files = [item["file"] for item in investigation["likely_files"]]
        assert all("node_modules" not in file for file in all_files)


def test_bugfix_artifacts_are_written_and_boundary_forbids_generated_paths():
    with tempfile.TemporaryDirectory() as td:
        app = Path(td) / "app"
        app.mkdir()
        (app / "Dashboard.tsx").write_text("console.log('dashboard')\n")
        out = Path(td) / "out"
        state = p.run_bugfix_planning(
            app,
            "Title: Dashboard fails\nConsole error: Dashboard.tsx",
            output_dir=out,
        )
        for artifact in p.BUGFIX_ARTIFACTS:
            assert (out / artifact).exists()
        boundary = state["boundary"]
        assert ".env" in boundary["forbidden_paths"]
        assert "node_modules" in boundary["forbidden_paths"]
        assert "venv" in boundary["forbidden_paths"]
        assert "runs" in boundary["forbidden_paths"]
        assert boundary["config_env_edits_allowed"] is False


def test_db_and_auth_edits_default_to_not_allowed_unless_clued():
    parsed = p.parse_bug_report("Actual: Dashboard data does not load")
    boundary = p.build_bugfix_boundary(parsed, {"likely_files": []})
    assert boundary["db_schema_edits_allowed"] is False
    assert boundary["auth_edits_allowed"] is False

    auth_parsed = p.parse_bug_report("Actual: Login redirect breaks after auth callback")
    auth_boundary = p.build_bugfix_boundary(auth_parsed, {"likely_files": []})
    assert auth_boundary["auth_edits_allowed"] is True


def test_existing_app_bugfix_planning_writes_run_state_fields():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        p.RUNS_DIR = Path(td) / "runs"
        app = Path(td) / "app"
        app.mkdir()
        (app / "Dashboard.tsx").write_text("loadCandidates\n")
        run_id = "run_fixture_bugfix"
        p.init_run(run_id, "bugfix fixture")
        state = p.run_existing_app_bugfix_planning(
            run_id,
            app,
            "Title: Dashboard does not load\nConsole error: Dashboard.tsx loadCandidates",
        )
        run_state = p.load_state(run_id)
        assert state["bugfix_mode"] is True
        assert run_state["bugfix_mode"] is True
        assert run_state["bug_title"] == "Dashboard does not load"
        assert run_state["bugfix_artifacts"] == p.BUGFIX_ARTIFACTS
        assert run_state["suspected_files_count"] >= 1
        assert "bug_report_summary.md" in run_state["artifacts"]
        p.RUNS_DIR = original_runs_dir


def test_bugfix_mode_and_feature_sprint_mode_conflict_cleanly():
    with tempfile.TemporaryDirectory() as td:
        app = Path(td) / "app"
        app.mkdir()
        bug = Path(td) / "bug.md"
        bug.write_text("Actual: broken")
        feature = Path(td) / "feature.md"
        feature.write_text("Add dashboard")
        result = subprocess.run(
            [
                sys.executable,
                "pipeline_mvp_builder.py",
                "--existing-app", str(app),
                "--upgrade-mode",
                "--bugfix-mode",
                "--bug-report", str(bug),
                "--feature-request", str(feature),
                "--feature-plan-only",
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "separate from feature sprint mode" in result.stdout


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
