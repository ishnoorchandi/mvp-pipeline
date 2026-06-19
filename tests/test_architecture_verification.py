"""
Deterministic tests for:
  A. verify_architecture() being constraint-aware (no false PASS/FAIL for frontend-only apps)
  B. no_api false-positive fix (React Context API, Browser API, Web API pass; backend phrases fail)

No OpenAI / DeepSeek calls.  verify_architecture() does run subprocess grep calls, but
these are standard system tools operating on temp directories, not network calls.

Run with:
    ./venv/bin/python3 tests/test_architecture_verification.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_mvp_builder import (
    detect_negative_constraints,
    check_requirements_consistency,
    verify_architecture,
)

# ── Shared constraint set for a frontend-only mood picker ─────────────────────

RAW_INPUT = (
    "Build a frontend-only mood picker app. No backend, no database, no login."
)
CLEAN_REQUIREMENTS = """# Clean Requirements: Mood Picker

## Requirements
1. Display five mood cards.
2. Frontend-only — no backend, no database, no login.
"""

FRONTEND_CONSTRAINTS = detect_negative_constraints(RAW_INPUT, "", CLEAN_REQUIREMENTS)


# ══════════════════════════════════════════════════════════════════════════════
#  Part A: no_api false-positive in consistency checker
# ══════════════════════════════════════════════════════════════════════════════

def test_react_context_api_passes():
    """'React Context API' is a frontend state concept — must not trigger no_api."""
    arch = """# Architecture

## Stack
React + Vite, frontend-only.

## State Management
State is managed using the React Context API for passing selected mood between components.

## Smoke Checks
- npm run build succeeds
"""
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": arch})
    assert ok is True, f"Expected 'React Context API' to pass, got:\n{report}"
    print("PASS: 'React Context API' does not trigger no_api violation")


def test_context_api_passes():
    """Bare 'Context API' (without 'React') must also pass."""
    line = "Mood state is shared via the Context API."
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": line})
    assert ok is True, f"Expected bare 'Context API' to pass, got:\n{report}"
    print("PASS: bare 'Context API' does not trigger no_api violation")


def test_browser_api_passes():
    """'Browser API' is a generic reference to web platform APIs — must pass."""
    line = "Animations use the Browser API for requestAnimationFrame."
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": line})
    assert ok is True, f"Expected 'Browser API' to pass, got:\n{report}"
    print("PASS: 'Browser API' does not trigger no_api violation")


def test_web_api_passes():
    """'Web API' is a generic browser platform reference — must pass."""
    line = "Colour picker uses the Web API EyeDropper interface."
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": line})
    assert ok is True, f"Expected 'Web API' to pass, got:\n{report}"
    print("PASS: 'Web API' does not trigger no_api violation")


def test_create_api_routes_fails():
    """'Create API routes' is an imperative backend instruction — must fail."""
    line = "Create API routes using Express."
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": line})
    assert ok is False, f"Expected 'Create API routes' to fail"
    print("PASS: 'Create API routes' correctly triggers no_api violation")


def test_call_api_endpoint_fails():
    """'Call /api/moods' in a build artifact — must fail."""
    line = "Call /api/moods to retrieve the mood list."
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": line})
    assert ok is False, f"Expected '/api/moods' to fail"
    print("PASS: 'Call /api/moods' correctly triggers no_api violation")


def test_fetch_api_path_fails():
    """fetch('/api/...') is a backend API call — must fail."""
    line = "fetch('/api/moods')"
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"build_prompt.txt": line})
    assert ok is False, f"Expected fetch('/api/...') to fail"
    print("PASS: fetch('/api/moods') correctly triggers no_api violation")


def test_backend_api_fails():
    """'Backend API' explicitly names backend scope — must fail."""
    line = "The Backend API serves mood data over HTTP."
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": line})
    assert ok is False, f"Expected 'Backend API' to fail"
    print("PASS: 'Backend API' correctly triggers no_api violation")


def test_rest_api_fails():
    """'Build REST API' is a backend build instruction — must fail."""
    line = "Build REST API endpoints for mood selection."
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": line})
    assert ok is False, f"Expected 'Build REST API endpoints' to fail"
    print("PASS: 'Build REST API endpoints' correctly triggers no_api violation")


def test_api_endpoint_fails():
    """Bare 'API endpoint' outside exclusion section — must fail."""
    line = "Set up an API endpoint for mood retrieval."
    ok, report = check_requirements_consistency(FRONTEND_CONSTRAINTS, {"ARCHITECTURE.md": line})
    assert ok is False, f"Expected 'API endpoint' to fail"
    print("PASS: 'Set up an API endpoint' correctly triggers no_api violation")


# ══════════════════════════════════════════════════════════════════════════════
#  Part B: verify_architecture() constraint-aware behaviour
# ══════════════════════════════════════════════════════════════════════════════

def test_frontend_only_arch_no_backend_required():
    """
    With frontend-only constraints, verify_architecture() must NOT produce a FAIL
    for missing backend files or missing fetch/axios calls.
    It should PASS because the app is correctly frontend-only.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        mvp_dir = Path(tmpdir)
        # Create a minimal frontend structure (src/ + package.json)
        (mvp_dir / "src").mkdir()
        (mvp_dir / "package.json").write_text('{"name":"mood-picker","version":"0.1.0"}')
        (mvp_dir / "src" / "App.tsx").write_text(
            "import React, { useState } from 'react';\n"
            "export default function App() { return <div>Mood Picker</div>; }\n"
        )

        result = verify_architecture(
            "test_frontend_only",
            mvp_dir,
            "Build a frontend-only mood picker. No backend, no database, no login.",
            constraints=FRONTEND_CONSTRAINTS,
        )

    assert "[FAIL] No backend server file found" not in result, (
        "Expected no backend FAIL for frontend-only app:\n" + result
    )
    assert "[FAIL] Frontend has no fetch/axios calls" not in result, (
        "Expected no fetch/axios FAIL for frontend-only app:\n" + result
    )
    assert "[FAIL] No localStorage" not in result, (
        "Expected no localStorage FAIL for no-database app:\n" + result
    )
    assert "[PASS] No fetch/axios calls found" in result, (
        "Expected PASS for absent fetch/axios in no-API app:\n" + result
    )
    assert "[PASS] Frontend source directory" in result, (
        "Expected PASS for frontend source existing:\n" + result
    )
    assert "RESULT: Architecture looks correct" in result, (
        "Expected architecture to be correct:\n" + result
    )
    print("PASS: frontend-only verify_architecture() correctly reports no backend/fetch/db failures")


def test_frontend_only_arch_no_constraints_fallback():
    """
    With no constraints and a frontend-only spec where 'api' only appears in
    negation context, the fallback keyword scan must NOT produce false needs_api=True.
    The spec says 'no backend', 'no api' — those bare words should not trigger
    backend/API checks.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        mvp_dir = Path(tmpdir)
        (mvp_dir / "src").mkdir()
        (mvp_dir / "package.json").write_text('{"name":"mood-picker"}')
        (mvp_dir / "src" / "App.tsx").write_text("export default function App() { return null; }")

        # No constraints passed — tests the fallback keyword scan
        result = verify_architecture(
            "test_fallback_scan",
            mvp_dir,
            "Build a mood picker. No api, no backend, no database.",
            constraints=None,
        )

    # The fallback scan must not match 'api'/'backend'/'database' inside negation phrases
    assert "[FAIL] No backend server file found" not in result, (
        "Fallback scan should not trigger backend check from 'No api, no backend' phrase:\n" + result
    )
    assert "[FAIL] Frontend has no fetch/axios calls" not in result, (
        "Fallback scan should not trigger fetch/axios check from 'No api' phrase:\n" + result
    )
    print("PASS: fallback keyword scan does not produce false positives from negation phrases")


def test_backend_required_arch_catches_missing_backend():
    """
    When a spec requires a backend (Flask/Express), verify_architecture() must
    still FAIL if no backend file is present.  Constraint-awareness must not
    suppress checks for apps that genuinely need a backend.
    """
    backend_constraints = detect_negative_constraints(
        "Build a mood picker with a Flask backend and PostgreSQL database.",
        "",
        "## Requirements\n1. REST API for mood selection.\n2. PostgreSQL database.\n",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        mvp_dir = Path(tmpdir)
        # Only frontend; no backend file
        (mvp_dir / "src").mkdir()
        (mvp_dir / "package.json").write_text('{"name":"mood-picker"}')
        (mvp_dir / "src" / "App.tsx").write_text("export default function App() { return null; }")

        result = verify_architecture(
            "test_backend_required",
            mvp_dir,
            "Build a mood picker with a Flask backend and PostgreSQL.",
            constraints=backend_constraints,
        )

    assert "[FAIL] No backend server file found" in result, (
        "Expected FAIL for missing backend file in backend-required app:\n" + result
    )
    print("PASS: backend-required app correctly FAILS when backend files are missing")


def test_backend_required_passes_when_backend_exists():
    """
    When a backend-required spec has all files in place, architecture check passes.
    """
    backend_constraints = detect_negative_constraints(
        "Build a mood picker with a Flask backend and React frontend.",
        "",
        "## Requirements\n1. Flask REST API.\n",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        mvp_dir = Path(tmpdir)
        # Backend file present
        (mvp_dir / "app.py").write_text(
            "from flask import Flask, jsonify\napp = Flask(__name__)\n"
            "@app.route('/api/moods')\ndef moods(): return jsonify([])\n"
        )
        # Frontend with fetch call
        (mvp_dir / "src").mkdir()
        (mvp_dir / "src" / "App.tsx").write_text(
            "const r = await fetch('/api/moods');\n"
        )

        result = verify_architecture(
            "test_backend_present",
            mvp_dir,
            "Build a mood picker with a Flask backend and React frontend.",
            constraints=backend_constraints,
        )

    assert "[PASS] Backend server file found" in result, (
        "Expected PASS for present backend file:\n" + result
    )
    assert "[FAIL] No backend server file found" not in result
    print("PASS: backend-required app correctly PASSES when backend files are present")


if __name__ == "__main__":
    # no_api false-positive tests (consistency checker)
    test_react_context_api_passes()
    test_context_api_passes()
    test_browser_api_passes()
    test_web_api_passes()
    test_create_api_routes_fails()
    test_call_api_endpoint_fails()
    test_fetch_api_path_fails()
    test_backend_api_fails()
    test_rest_api_fails()
    test_api_endpoint_fails()

    # verify_architecture() constraint-aware tests
    test_frontend_only_arch_no_backend_required()
    test_frontend_only_arch_no_constraints_fallback()
    test_backend_required_arch_catches_missing_backend()
    test_backend_required_passes_when_backend_exists()

    print("\nALL TESTS PASSED")
