"""Backend Change Boundary + Backend Smoke Checks.

Safety layer that lets the pipeline reason about backend changes BEFORE any
backend bugfix/build step touches code. --backend-boundary writes boundary
artifacts only; --backend-smoke-checks defaults to plan-only and only ever
executes explicit, safe commands. Never runs migrations, DB writes, seed/
reset commands, or production commands automatically.

Fixture repos only — never touches real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


# ── 1. DB/schema edits forbidden by default ─────────────────────────────────

def test_db_schema_edits_forbidden_by_default():
    with tempfile.TemporaryDirectory() as td:
        boundary = p.build_backend_change_boundary(Path(td), feature_request_text="Fix the dashboard loading spinner")
        assert boundary["db_schema_edits_allowed"] is False


# ── 2. DB/schema edits allowed only when explicitly required ───────────────

def test_db_schema_edits_allowed_with_explicit_clue():
    with tempfile.TemporaryDirectory() as td:
        boundary = p.build_backend_change_boundary(
            Path(td), feature_request_text="We need to add a new column to the users database table — a schema migration is required.",
        )
        assert boundary["db_schema_edits_allowed"] is True


# ── 3. Auth edits forbidden by default ──────────────────────────────────────

def test_auth_edits_forbidden_by_default():
    with tempfile.TemporaryDirectory() as td:
        boundary = p.build_backend_change_boundary(Path(td), feature_request_text="Fix the orders list pagination")
        assert boundary["auth_edits_allowed"] is False


# ── 4. Auth edits allowed only when auth clue exists ────────────────────────

def test_auth_edits_allowed_with_explicit_clue():
    with tempfile.TemporaryDirectory() as td:
        boundary = p.build_backend_change_boundary(
            Path(td), bug_report_text="Login session expires immediately after JWT token refresh.",
        )
        assert boundary["auth_edits_allowed"] is True


# ── 5. Migration edits forbidden by default ─────────────────────────────────

def test_migration_edits_forbidden_by_default():
    with tempfile.TemporaryDirectory() as td:
        boundary = p.build_backend_change_boundary(Path(td), feature_request_text="Fix a typo in the footer")
        assert boundary["migration_edits_allowed"] is False


def test_migration_edits_allowed_with_explicit_clue():
    with tempfile.TemporaryDirectory() as td:
        boundary = p.build_backend_change_boundary(
            Path(td), feature_request_text="Run an alembic migration to add the new orders table.",
        )
        assert boundary["migration_edits_allowed"] is True


# ── 6. Route signature changes forbidden by default ─────────────────────────

def test_route_signature_edits_forbidden_by_default():
    with tempfile.TemporaryDirectory() as td:
        boundary = p.build_backend_change_boundary(Path(td), feature_request_text="Add a loading spinner")
        assert boundary["route_signature_edits_allowed"] is False


# ── 7. Endpoint deletion always forbidden, config/env always forbidden ─────

def test_delete_endpoint_and_config_env_always_forbidden():
    with tempfile.TemporaryDirectory() as td:
        boundary = p.build_backend_change_boundary(
            Path(td), feature_request_text="Please delete the old /api/legacy endpoint and update the .env config.",
        )
        assert boundary["delete_endpoint_allowed"] is False
        assert boundary["config_env_edits_allowed"] is False


# ── 8. Backend boundary artifacts are written ───────────────────────────────

def test_backend_boundary_artifacts_written():
    with tempfile.TemporaryDirectory() as td:
        app_root = Path(td) / "app"
        app_root.mkdir()
        (app_root / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n\n"
            "@app.route('/api/users')\ndef list_users():\n    return {}\n"
        )
        out_dir = Path(td) / "out"
        boundary = p.run_backend_change_boundary(app_root, out_dir)
        for fname in p.BACKEND_BOUNDARY_ARTIFACTS:
            assert (out_dir / fname).exists(), f"missing {fname}"
        md = (out_dir / "backend_change_boundary.md").read_text(encoding="utf-8")
        assert "Backend boundary only" in md
        state = json.loads((out_dir / "backend_change_boundary.json").read_text(encoding="utf-8"))
        assert state["status"] == boundary["status"]


# ── 9. Backend smoke plan classifies safe vs forbidden checks ──────────────

def test_smoke_plan_classifies_safe_vs_forbidden():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app.py").write_text("print('hello')\n")
        (root / "package.json").write_text(json.dumps({
            "scripts": {"test": "jest", "build": "vite build", "migrate": "knex migrate:latest"},
        }))
        plan = p.detect_backend_smoke_plan(root)
        safe_names = {c["name"] for c in plan["safe_automatic_checks"]}
        forbidden_names = {c["name"] for c in plan["forbidden_automatic_checks"]}
        assert any("Python compile check" in n for n in safe_names)
        assert any("npm test" in n for n in safe_names)
        assert any("npm build" in n for n in safe_names)
        assert any("migrate" in n for n in forbidden_names)


# ── 10. Smoke plan refuses migrations/seed/reset commands automatically ────

def test_smoke_plan_refuses_migration_seed_reset_automatically():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "package.json").write_text(json.dumps({
            "scripts": {"migrate": "knex migrate:latest", "seed": "knex seed:run", "reset": "knex migrate:rollback --all"},
        }))
        (root / "manage.py").write_text("# django manage.py\n")
        plan = p.detect_backend_smoke_plan(root)
        forbidden_commands = {c["command"] for c in plan["forbidden_automatic_checks"]}
        assert any("migrate" in c for c in forbidden_commands)
        assert any("seed" in c for c in forbidden_commands)
        assert any("reset" in c for c in forbidden_commands)
        assert any("manage.py migrate" in c for c in forbidden_commands)
        for item in plan["safe_automatic_checks"]:
            assert "migrate" not in item["command"] or "manage.py migrate" not in item["command"]


# ── 11. Smoke execution runs explicit safe pytest command in fixture repo ──

def test_smoke_execution_runs_explicit_safe_test_command():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "test_sample.py").write_text(
            "def test_ok():\n    assert 1 + 1 == 2\n"
        )
        plan = p.detect_backend_smoke_plan(root, test_command="python -m pytest -q")
        results = p.run_backend_smoke_checks(root, plan, test_command="python -m pytest -q")
        executed = [r for r in results if r["check"] == "User-provided backend test command"]
        assert len(executed) == 1
        assert executed[0]["status"] == "pass"
        assert executed[0]["exit_code"] == 0
        # The always-present "never run production commands" placeholder is recorded as skip, not run.
        skipped = [r for r in results if r["safety_classification"] == "forbidden"]
        assert all(r["status"] == "skip" for r in skipped)


# ── 12. Smoke execution skips/blocks unsafe (forbidden) commands ───────────

def test_smoke_execution_skips_forbidden_commands():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "package.json").write_text(json.dumps({"scripts": {"migrate": "knex migrate:latest"}}))
        plan = p.detect_backend_smoke_plan(root)
        results = p.run_backend_smoke_checks(root, plan)
        skipped = [r for r in results if r["safety_classification"] == "forbidden"]
        assert skipped, "expected at least one forbidden check recorded"
        for r in skipped:
            assert r["status"] == "skip"
            assert r["exit_code"] is None


# ── 13. Boundary enforcement detects forbidden path violations ─────────────

def test_enforcement_detects_forbidden_path_violation():
    boundary = {
        "allowed_backend_files": ["backend/app.py"],
        "allowed_frontend_api_client_files": [],
        "allowed_test_files": [],
        "allowed_directories": ["backend"],
        "protected_files": [],
        "forbidden_paths": p.BACKEND_BOUNDARY_FORBIDDEN_PATHS,
        "db_schema_edits_allowed": False,
        "auth_edits_allowed": False,
        "config_env_edits_allowed": False,
    }
    result = p.check_backend_change_boundary(boundary, ["backend/app.py", ".env", "node_modules/pkg/index.js"])
    assert result["status"] == "fail"
    assert ".env" in result["forbidden_paths_touched"]
    assert "node_modules/pkg/index.js" in result["forbidden_paths_touched"]
    assert "backend/app.py" in result["allowed_files_matched"]


# ── 14. Boundary enforcement detects protected file touches ────────────────

def test_enforcement_detects_protected_file_touch():
    boundary = {
        "allowed_backend_files": ["backend/app.py"],
        "allowed_frontend_api_client_files": [],
        "allowed_test_files": [],
        "allowed_directories": ["backend"],
        "protected_files": ["backend/critical_auth.py"],
        "forbidden_paths": p.BACKEND_BOUNDARY_FORBIDDEN_PATHS,
        "db_schema_edits_allowed": False,
        "auth_edits_allowed": False,
        "config_env_edits_allowed": False,
    }
    result = p.check_backend_change_boundary(boundary, ["backend/critical_auth.py"])
    assert result["status"] == "fail"
    assert "backend/critical_auth.py" in result["protected_files_touched"]


# ── 15. Existing App Upgrade writes backend boundary/smoke fields ──────────

def test_existing_app_upgrade_writes_backend_boundary_and_smoke_state():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        p.RUNS_DIR = runs_dir
        try:
            app_root = Path(td) / "app"
            app_root.mkdir()
            (app_root / "app.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n\n"
                "@app.route('/api/ping')\ndef ping():\n    return 'pong'\n"
            )

            run_id = "run_fixture_backend_boundary"
            p.init_run(run_id, "fixture existing app upgrade")

            boundary = p.run_existing_app_backend_change_boundary(run_id, app_root)
            assert boundary["status"] in ("ready", "warning")

            outcome = p.run_existing_app_backend_smoke(run_id, app_root, plan_only=True)
            assert outcome["results"] is None

            state = p.load_state(run_id)
            assert state["backend_boundary_status"] == boundary["status"]
            assert state["backend_boundary_artifacts"] == p.BACKEND_BOUNDARY_ARTIFACTS
            assert isinstance(state["backend_safe_to_edit"], bool)
            assert state["backend_smoke_status"] == "plan_only"
            assert state["backend_smoke_artifacts"] == p.BACKEND_SMOKE_PLAN_ARTIFACTS
            assert isinstance(state["backend_safe_to_run_checks"], bool)
        finally:
            p.RUNS_DIR = original_runs_dir


# ── 16. Bugfix mode references backend boundary for backend/API bugs ───────

def test_bugfix_mode_generates_backend_boundary_for_backend_bug():
    with tempfile.TemporaryDirectory() as td:
        app_root = Path(td)
        (app_root / "backend").mkdir()
        (app_root / "backend" / "orders_api.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n\n"
            "@app.route('/api/orders', methods=['GET'])\ndef list_orders():\n    return []\n"
        )
        report = (
            "Title: Orders page is empty\n"
            "Backend error: GET /api/orders returns 500\n"
            "Affected endpoint: /api/orders\n"
        )
        out_dir = Path(td) / "bugfix_out"
        state = p.run_bugfix_planning(app_root, report, output_dir=out_dir)
        assert state["bug_category"] in ("backend", "api", "mixed")
        assert state.get("backend_relevant") is True
        for fname in p.BACKEND_BOUNDARY_ARTIFACTS:
            assert (out_dir / fname).exists(), f"missing {fname}"
        minimal_fix_plan = (out_dir / "minimal_fix_plan.md").read_text(encoding="utf-8")
        assert "Backend Smoke Checks To Run" in minimal_fix_plan
        suspected_md = (out_dir / "suspected_files.md").read_text(encoding="utf-8")
        assert "backend boundary" in suspected_md.lower()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
