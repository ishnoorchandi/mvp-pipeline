"""Backend Inventory + Backend Route Map.

Read-only static analysis: never rewrites backend code, never makes app
changes, never commits/pushes/opens a PR. Lightweight regex/file-walk only.

Fixture repos only — never touches real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


# ── 1. Detects Flask route ──────────────────────────────────────────────────

def test_detects_flask_route():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/api/users', methods=['GET', 'POST'])\n"
            "def list_users():\n"
            "    return {}\n"
        )
        routes = p.scan_backend_routes(root, root)
        methods = {(r["method"], r["path"]) for r in routes}
        assert ("GET", "/api/users") in methods
        assert ("POST", "/api/users") in methods
        flask_route = next(r for r in routes if r["method"] == "GET")
        assert flask_route["framework"] == "Flask"
        assert flask_route["handler"] == "list_users"
        assert flask_route["file"] == "app.py"


# ── 2. Detects FastAPI route ─────────────────────────────────────────────────

def test_detects_fastapi_route():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.py").write_text(
            "from fastapi import FastAPI, APIRouter\n"
            "app = FastAPI()\n"
            "router = APIRouter()\n\n"
            "@router.post('/api/orders')\n"
            "def create_order():\n"
            "    return {}\n"
        )
        routes = p.scan_backend_routes(root, root)
        assert any(r["method"] == "POST" and r["path"] == "/api/orders" and r["framework"] == "FastAPI" for r in routes)
        match = next(r for r in routes if r["path"] == "/api/orders")
        assert match["handler"] == "create_order"


# ── 3. Detects Express route ────────────────────────────────────────────────

def test_detects_express_route():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "server.js").write_text(
            "const express = require('express');\n"
            "const app = express();\n"
            "const router = express.Router();\n"
            "router.get('/api/orders', listOrders);\n"
            "app.post('/api/orders', createOrder);\n"
        )
        routes = p.scan_backend_routes(root, root)
        methods = {(r["method"], r["path"], r["framework"]) for r in routes}
        assert ("GET", "/api/orders", "Express") in methods
        assert ("POST", "/api/orders", "Express") in methods
        get_route = next(r for r in routes if r["method"] == "GET")
        assert get_route["handler"] == "listOrders"


# ── 4. Detects Next API route ───────────────────────────────────────────────

def test_detects_next_pages_api_route():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pages" / "api").mkdir(parents=True)
        (root / "pages" / "api" / "hello.ts").write_text(
            "export default function handler(req, res) {\n"
            "  if (req.method === 'GET') { res.send('ok'); }\n"
            "}\n"
        )
        routes = p.scan_backend_routes(root, root)
        assert any(r["path"] == "/api/hello" and r["framework"] == "Next.js API routes" for r in routes)


def test_detects_next_app_api_route():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app" / "api" / "users").mkdir(parents=True)
        (root / "app" / "api" / "users" / "route.ts").write_text(
            "export async function GET(req) {\n"
            "  return Response.json([]);\n"
            "}\n"
        )
        routes = p.scan_backend_routes(root, root)
        assert any(r["path"] == "/api/users" and r["method"] == "GET" and r["framework"] == "Next.js API routes" for r in routes)


# ── 5. Detects frontend fetch/axios call ────────────────────────────────────

def test_detects_frontend_fetch_call():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "api.js").write_text(
            "export function getUsers() {\n"
            "  return fetch('/api/users').then(r => r.json());\n"
            "}\n"
        )
        calls = p.scan_frontend_api_calls(root, root)
        assert len(calls) == 1
        assert calls[0]["endpoint"] == "/api/users"
        assert calls[0]["method"] == "GET"
        assert calls[0]["caller"] == "getUsers"


def test_detects_frontend_axios_call():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "client.ts").write_text(
            "export async function createOrder(payload) {\n"
            "  return axios.post('/api/orders', payload);\n"
            "}\n"
        )
        calls = p.scan_frontend_api_calls(root, root)
        assert len(calls) == 1
        assert calls[0]["method"] == "POST"
        assert calls[0]["endpoint"] == "/api/orders"
        assert calls[0]["caller"] == "createOrder"


# ── 6. Matches frontend /api/... call to backend route ──────────────────────

def test_matches_frontend_call_to_backend_route():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "backend").mkdir()
        (root / "backend" / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/api/users', methods=['GET'])\n"
            "def list_users():\n"
            "    return {}\n"
        )
        (root / "frontend").mkdir()
        (root / "frontend" / "api.js").write_text(
            "export function getUsers() {\n"
            "  return fetch('/api/users').then(r => r.json());\n"
            "}\n"
        )
        routes = p.scan_backend_routes(root, root)
        calls = p.scan_frontend_api_calls(root, root)
        p.match_frontend_calls_to_routes(calls, routes)
        assert calls[0]["matched_backend_route"] == "GET /api/users (backend/app.py)"
        assert calls[0]["match_confidence"] == "high"


# ── 7. Extracts env var names without values ────────────────────────────────

def test_extracts_env_var_names_without_values():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "config.py").write_text(
            "import os\n"
            "DB_PASSWORD = os.environ['DB_PASSWORD']\n"
            "API_KEY = os.getenv('API_KEY')\n"
        )
        (root / "config.ts").write_text("const url = process.env.API_URL;\n")
        (root / ".env.example").write_text("SECRET_TOKEN=supersecretvalue\nANOTHER_VAR=123\n")

        env_vars = p.scan_env_requirements(root)
        assert "DB_PASSWORD" in env_vars
        assert "API_KEY" in env_vars
        assert "API_URL" in env_vars
        assert "SECRET_TOKEN" in env_vars
        assert "ANOTHER_VAR" in env_vars
        # Never store/expose values.
        dumped = json.dumps(env_vars)
        assert "supersecretvalue" not in dumped


# ── 8. Skips generated folders ──────────────────────────────────────────────

def test_skips_generated_folders():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "node_modules" / "pkg").mkdir(parents=True)
        (root / "node_modules" / "pkg" / "index.js").write_text(
            "app.get('/api/should-not-appear', handler);\n"
        )
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "cached.py").write_text(
            "@app.route('/api/also-skip')\ndef cached(): pass\n"
        )
        (root / "real.py").write_text(
            "@app.route('/api/real')\ndef real(): pass\n"
        )
        routes = p.scan_backend_routes(root, root)
        paths = {r["path"] for r in routes}
        assert "/api/real" in paths
        assert "/api/should-not-appear" not in paths
        assert "/api/also-skip" not in paths


# ── 9. Writes all backend inventory artifacts ───────────────────────────────

def test_writes_all_backend_inventory_artifacts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "app"
        root.mkdir()
        (root / "requirements.txt").write_text("flask\n")
        (root / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/api/users')\n"
            "def list_users():\n"
            "    return {}\n"
        )
        (root / "api.js").write_text("fetch('/api/users');\n")
        out_dir = Path(td) / "out"

        inv = p.run_backend_inventory(root, out_dir)
        for fname in (
            "backend_inventory.md", "backend_inventory_state.json", "backend_route_map.md",
            "frontend_api_client_map.md", "backend_data_flow.md", "backend_env_requirements.md",
            "backend_test_plan.md",
        ):
            assert (out_dir / fname).exists(), f"missing {fname}"

        content = (out_dir / "backend_inventory.md").read_text(encoding="utf-8")
        assert "Inventory only" in content
        assert "Flask" in content

        state = json.loads((out_dir / "backend_inventory_state.json").read_text(encoding="utf-8"))
        assert state["route_count"] == inv["route_count"]


# ── 10. Existing App Upgrade writes run_state backend inventory fields ─────

def test_existing_app_upgrade_backend_inventory_helper_writes_state():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        p.RUNS_DIR = runs_dir
        try:
            app_root = Path(td) / "app"
            app_root.mkdir()
            (app_root / "requirements.txt").write_text("flask\n")
            (app_root / "app.py").write_text(
                "from flask import Flask\n"
                "app = Flask(__name__)\n\n"
                "@app.route('/api/ping')\n"
                "def ping():\n"
                "    return 'pong'\n"
            )

            run_id = "run_fixture_backend_inventory"
            p.init_run(run_id, "fixture existing app upgrade")

            inv = p.run_existing_app_backend_inventory(run_id, app_root)
            assert inv["route_count"] == 1

            rdir = p.run_dir(run_id)
            for fname in p.BACKEND_INVENTORY_ARTIFACTS:
                assert (rdir / fname).exists(), f"missing {fname}"

            state = p.load_state(run_id)
            assert state["backend_inventory_mode"] is True
            assert state["backend_route_count"] == 1
            assert state["frontend_api_call_count"] == 0
            assert isinstance(state["env_var_count"], int)
            assert state["backend_inventory_artifacts"] == p.BACKEND_INVENTORY_ARTIFACTS
            assert "backend_inventory.md" in state["artifacts"]
        finally:
            p.RUNS_DIR = original_runs_dir


# ── 11. Bugfix mode ranks endpoint-matched backend files higher ────────────

def test_bugfix_mode_ranks_endpoint_matched_backend_file_higher():
    with tempfile.TemporaryDirectory() as td:
        app_root = Path(td)
        (app_root / "backend").mkdir()
        # No bug-report term appears in this file's content or name, so the plain
        # term-based investigate_bugfix_repo scan would never surface it on its own.
        (app_root / "backend" / "orders_api.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/api/orders', methods=['GET'])\n"
            "def list_orders():\n"
            "    return []\n"
        )
        report = (
            "Title: Orders page is empty\n"
            "Backend error: GET /api/orders returns 500\n"
            "Affected endpoint: /api/orders\n"
        )
        parsed = p.parse_bug_report(report)
        investigation = p.investigate_bugfix_repo(app_root, parsed)
        before = next((item for item in investigation["likely_files"] if item["file"] == "backend/orders_api.py"), None)
        before_score = before["score"] if before else 0
        before_confidence = before["confidence"] if before else None
        assert before_confidence != "high"

        inventory = p.generate_backend_inventory_for_bugfix(app_root)
        enriched = p.enrich_bugfix_investigation_with_inventory(parsed, investigation, inventory)
        top_files = [item["file"] for item in enriched["likely_files"]]
        assert "backend/orders_api.py" in top_files
        match = next(item for item in enriched["likely_files"] if item["file"] == "backend/orders_api.py")
        # Endpoint-matched backend file must rank higher (better score, "high" confidence)
        # after inventory enrichment than the plain term-based scan alone produced.
        assert match["confidence"] == "high"
        assert match["score"] > before_score
        assert "matches backend route" in match["reason"]

        # End-to-end through run_bugfix_planning: the file should show up in
        # suspected_files.md with route evidence in its reason column.
        out_dir = Path(td) / "bugfix_out"
        state = p.run_bugfix_planning(app_root, report, output_dir=out_dir)
        suspected_md = (out_dir / "suspected_files.md").read_text(encoding="utf-8")
        assert "backend/orders_api.py" in suspected_md
        assert "matches backend route" in suspected_md
        assert state["suspected_files_count"] >= 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
