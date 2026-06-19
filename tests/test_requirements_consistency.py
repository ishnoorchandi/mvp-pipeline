"""
Deterministic test for the requirements-consistency safety check (Phase 2).

Regression case: a frontend-only mood picker app whose planning artifacts
previously got a hallucinated backend/database/login bolted on by GPT.

This test exercises only the deterministic functions (detect_negative_constraints,
check_requirements_consistency) — no GPT/API calls, no network.

Run with:
    ./venv/bin/python3 tests/test_requirements_consistency.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_mvp_builder import (
    detect_negative_constraints,
    check_requirements_consistency,
    generate_smoke_checks_doc,
)

RAW_INPUT = (
    "Build a frontend-only mood picker app. The user should see five mood cards, "
    "click one, and see the selected mood displayed. No backend, no database, no login."
)

CLEAN_REQUIREMENTS = """# Clean Requirements: Mood Picker

## Requirements
1. Display five mood cards.
2. Clicking a card shows the selected mood.
3. Frontend-only — no backend, no database, no login.

## Acceptance Criteria
1. User can click a mood card and see it selected.
"""


def test_constraints_detected():
    constraints = detect_negative_constraints(RAW_INPUT, "", CLEAN_REQUIREMENTS)
    assert constraints["frontend_only"] is True
    assert constraints["no_backend"] is True
    assert constraints["no_database"] is True
    assert constraints["no_login"] is True
    assert constraints["no_auth"] is True
    assert constraints["no_api"] is True
    print("PASS: negative constraints correctly detected from mood picker input")
    return constraints


def test_bad_artifacts_are_caught(constraints):
    """Simulates the exact failure the user reported — GPT inventing a backend."""
    bad_architecture = """# Architecture

## Stack
Flask backend with PostgreSQL database, React frontend.

## File / Folder Boundaries
backend/app.py owns all API routes and DB access.

## Forbidden Shortcuts
- No localStorage for persistence

## Smoke Checks
- curl http://127.0.0.1:5001/api/moods returns 200
- psql -d mood_db -c "select * from moods" returns rows

## Deployment Assumptions
Local only, macOS, backend on port 5001.
"""
    bad_smoke_checks = "## Smoke Checks\n- psql -d mood_db -c \"select * from moods;\"\n- curl http://127.0.0.1:5001/api/moods\n"
    bad_build_prompt = (
        "Build the following MVP locally:\nCreate backend/app.py using Flask with a "
        "PostgreSQL database and a login system, plus API endpoints for mood selection.\n"
    )

    ok, report = check_requirements_consistency(
        constraints,
        {
            "ARCHITECTURE.md": bad_architecture,
            "smoke_checks.md": bad_smoke_checks,
            "build_prompt.txt": bad_build_prompt,
        },
    )
    assert ok is False, "Expected violations to be detected in the hallucinated-backend artifacts"
    assert "flask" in report.lower()
    assert "postgres" in report.lower()
    print("PASS: hallucinated backend/database/login artifacts are correctly flagged")
    print(report)


def test_good_artifacts_pass(constraints):
    """The correct, frontend-only artifacts must NOT be flagged."""
    good_architecture = """# Architecture

## Stack
React + Vite, no backend, no database — purely client-side state.

## File / Folder Boundaries
src/components/MoodCard.tsx renders each card; src/App.tsx holds the selected-mood state.
There is no backend or database in this project.

## Forbidden Shortcuts
- Do not add a backend or database — none is required.

## Smoke Checks
- npm run build succeeds
- Clicking a mood card updates the displayed selection in the browser

## Deployment Assumptions
Local only, macOS, no Docker, no backend, run with npm run dev on port 5173.
"""
    good_smoke_checks = (
        "## Smoke Checks\n- npm run build succeeds\n"
        "- Clicking a mood card updates the displayed selection\n"
    )
    good_build_prompt = (
        "Build the following MVP locally:\nCreate a React + Vite frontend-only app with "
        "five mood cards. No backend, no database, no login. Selecting a card updates "
        "local component state only.\n"
    )

    ok, report = check_requirements_consistency(
        constraints,
        {
            "ARCHITECTURE.md": good_architecture,
            "smoke_checks.md": good_smoke_checks,
            "build_prompt.txt": good_build_prompt,
        },
    )
    assert ok is True, f"Expected no violations for correct frontend-only artifacts:\n{report}"
    print("PASS: correct frontend-only artifacts are not flagged")
    print(report)


def test_run_017_no_inclusion_phrase_passes(constraints):
    """
    Regression for Phase 2.5: "No inclusion of X" is a prohibition, not an
    instruction — even when the forbidden term ("database") sits far enough
    into the sentence that the old fixed-width negation window missed it.
    """
    line = "- No inclusion of any form of server-side logic or database interactions."
    ok, report = check_requirements_consistency(constraints, {"ARCHITECTURE.md": line})
    assert ok is True, f"Expected 'No inclusion of...database interactions' to pass, got:\n{report}"
    print("PASS: run_017's 'No inclusion of ... database interactions' line no longer blocks the build")


def test_prohibition_phrasing(constraints):
    """Prohibitions (do not / does not / must not / should not + verb) must pass;
    the corresponding bare imperative instructions must still fail."""
    safe_lines = [
        "No inclusion of any form of server-side logic or database interactions.",
        "No inclusion of database interactions.",
        "No inclusion of backend services.",
        "No inclusion of login or authentication.",
        "Do not include database interactions.",
        "Do not add backend services.",
        "Do not implement login.",
        "Must not include API calls.",
        "Should not use a database.",
    ]
    for line in safe_lines:
        ok, report = check_requirements_consistency(constraints, {"mvp_spec.md": line})
        assert ok is True, f"Expected prohibition to pass: {line!r}\n{report}"

    bad_lines = [
        "Include database interactions.",
        "Add backend services.",
        "Implement login.",
        "Use a database.",
        "Create API calls.",
        "Set up authentication.",
    ]
    for line in bad_lines:
        ok, report = check_requirements_consistency(constraints, {"mvp_spec.md": line})
        assert ok is False, f"Expected imperative instruction to fail: {line!r}\n{report}"
    print("PASS: prohibition phrasing passes, bare imperative instructions still fail")


def test_run_016_out_of_scope_lines_pass(constraints):
    """
    Regression for Phase 2.5: the exact lines that blocked run_016 are safe
    out-of-scope/exclusion bullets, not build instructions. They must pass
    when they appear under an Out of Scope heading.
    """
    spec_with_out_of_scope = """# MVP Spec: Mood Picker

## Key Features (MVP scope only)
1. Display five mood cards.
2. Clicking a card shows the selected mood.

## Out of Scope (V1)
- Any backend functionality, including databases or APIs.
- Storage of user selections beyond the immediate session (e.g., persisting data to a database or local storage).
- User authentication or login systems.
"""
    ok, report = check_requirements_consistency(constraints, {"mvp_spec.md": spec_with_out_of_scope})
    assert ok is True, f"Expected Out of Scope bullets to pass, got:\n{report}"
    print("PASS: run_016's exact Out of Scope lines no longer block the build")


def test_safe_exclusion_examples_pass(constraints):
    """The safe examples enumerated in the Phase 2.5 request must all pass."""
    safe_lines = [
        "- Any backend functionality, including databases or APIs.",
        "- User authentication or login systems.",
        "- Storage of user selections beyond the immediate session, such as persisting data to a database.",
        "- Backend services are excluded.",
        "- Login is not included.",
        "- No database is required.",
    ]
    spec = "# MVP Spec: Mood Picker\n\n## Out of Scope (V1)\n" + "\n".join(safe_lines) + "\n"
    ok, report = check_requirements_consistency(constraints, {"mvp_spec.md": spec})
    assert ok is True, f"Expected all safe exclusion examples to pass, got:\n{report}"
    print("PASS: all enumerated safe exclusion examples pass")


def test_blocked_instruction_examples_still_fail(constraints):
    """The bad examples enumerated in the Phase 2.5 request must each still be caught,
    even though they use similar vocabulary to the safe exclusion examples — and even
    if (adversarially) placed under an Out of Scope heading, since they are imperative
    build instructions rather than exclusions."""
    bad_lines = [
        "Build a backend service.",
        "Create a PostgreSQL database.",
        "Add login functionality.",
        "Set up authentication.",
        "Create API routes.",
        "Run psql to verify rows.",
        "Use Flask or Express for the backend.",
    ]
    for bad_line in bad_lines:
        # Outside any section.
        ok, report = check_requirements_consistency(constraints, {"mvp_spec.md": bad_line})
        assert ok is False, f"Expected to fail: {bad_line!r}\n{report}"
        # Even under an Out of Scope heading — these are still instructions, not exclusions.
        spec = f"# MVP Spec\n\n## Out of Scope (V1)\n- {bad_line}\n"
        ok, report = check_requirements_consistency(constraints, {"mvp_spec.md": spec})
        assert ok is False, f"Expected to fail even under Out of Scope: {bad_line!r}\n{report}"
    print("PASS: all enumerated blocked-instruction examples are still caught")


def test_express_word_vs_express_framework(constraints):
    """
    Regression for Phase 2.5: the plain English word "express" (as in "express
    a mood") must not be mistaken for the Express.js backend framework.
    """
    safe_text = "Enable users to express and visualize their current mood."
    ok, report = check_requirements_consistency(constraints, {"mvp_spec.md": safe_text})
    assert ok is True, f"Expected plain English 'express' to pass, got:\n{report}"
    print("PASS: plain English 'express' does not trigger a backend violation")

    bad_text = "Set up an Express.js backend server."
    ok, report = check_requirements_consistency(constraints, {"mvp_spec.md": bad_text})
    assert ok is False, "Expected 'Express.js backend server' to be flagged"
    assert "express" in report.lower()
    print("PASS: 'Express.js backend server' is still correctly flagged")


def test_bad_spec_with_psql_is_blocked(constraints):
    """Regression for Phase 2.5: mvp_spec.md itself must be checked, not just the other artifacts."""
    bad_spec = """# MVP Spec: Mood Picker

## Backend / API
- GET /api/moods → returns list of moods
- POST /api/moods/select → records the selected mood

## Database
- moods table: id, name, emoji
- selections table: id, mood_id, created_at

## Technical Proof Requirements
- curl http://127.0.0.1:5001/api/moods → returns JSON array
- psql -d mood_picker_db -c "SELECT * FROM selections;" → shows selection rows

## Out of Scope (V1)
Multi-user accounts.
"""
    ok, report = check_requirements_consistency(
        constraints,
        {"mvp_spec.md": bad_spec},
    )
    assert ok is False, "Expected mvp_spec.md with psql/API/database to be blocked"
    assert "psql" in report.lower()
    assert "mvp_spec.md" in report
    print("PASS: bad mvp_spec.md with psql/API/database is blocked")


def test_safe_negated_phrases_pass(constraints):
    """Phrases that correctly describe an exclusion must NOT be flagged as violations."""
    safe_spec = """# MVP Spec: Mood Picker

## Backend / API
No backend is required.

## Database
No database is required.

## Acceptance Criteria
1. No login is included. No API is required.
2. The app works without backend, without database, and without auth.
3. Authentication is not included; any login flow is excluded from this MVP.

## Technical Proof Requirements
- npm run build → succeeds with no errors
- grep -r "fetch(\\|axios" frontend/src/ → returns EMPTY (confirms no backend API calls were added, none required)
"""
    ok, report = check_requirements_consistency(
        constraints,
        {"mvp_spec.md": safe_spec},
    )
    assert ok is True, f"Expected safe exclusion phrasing to pass, got:\n{report}"
    print("PASS: safe negated/exclusion phrases do not trigger violations")


def test_frontend_only_smoke_checks_doc_has_no_backend_boilerplate(constraints):
    """generate_smoke_checks_doc() must not emit API/DB/backend boilerplate for frontend-only apps."""
    spec_with_leftover_backend_lines = """# MVP Spec: Mood Picker

## Technical Proof Requirements
- curl http://127.0.0.1:5001/api/moods → returns JSON array
- psql -d mood_picker_db -c "SELECT * FROM moods;" → shows mood rows
- npm run build → succeeds with no errors
"""
    arch_with_leftover_backend_lines = """# Architecture

## Smoke Checks
- Flask backend responds on port 5001
- npm run build succeeds
"""
    doc = generate_smoke_checks_doc(
        spec_with_leftover_backend_lines, arch_with_leftover_backend_lines, constraints
    )
    lowered = doc.lower()
    for forbidden in ("flask", "psql", "curl http", "/api/", "api endpoint"):
        assert forbidden not in lowered, f"smoke_checks.md still contains forbidden term: {forbidden}"
    assert "npm install" in lowered
    assert "npm run build" in lowered

    ok, report = check_requirements_consistency(constraints, {"smoke_checks.md": doc})
    assert ok is True, f"Expected generated frontend-only smoke_checks.md to pass, got:\n{report}"
    print("PASS: frontend-only smoke_checks.md has no API/DB/backend boilerplate")


def test_run_019_forbidden_shortcuts_section_passes(constraints):
    """
    Regression for run_019: the exact lines that blocked the build appear under
    ## Forbidden Shortcuts in ARCHITECTURE.md.  They describe actions that are
    PROHIBITED from the build, not instructions to perform those actions.
    They must pass the consistency checker.
    """
    arch_text = """# Architecture

## Stack
React + Vite, purely client-side.

## File / Folder Boundaries
src/components/MoodCard.tsx renders each card; src/App.tsx holds selected-mood state.

## Forbidden Shortcuts
- Using localStorage instead of state management for mood selection.
- Mocking data instead of implementing the real selection functionality.
- Introducing any database connections or API calls.
- Implementing user authentication or account features.

## Smoke Checks
- npm run build succeeds
- Clicking a mood card updates the displayed selection

## Deployment Assumptions
Local only, macOS, no Docker, port 5173, npm run dev.
"""
    ok, report = check_requirements_consistency(constraints, {"ARCHITECTURE.md": arch_text})
    assert ok is True, f"Expected Forbidden Shortcuts section to pass, got:\n{report}"
    print("PASS: run_019's Forbidden Shortcuts section lines no longer block the build")


def test_forbidden_section_variants_all_pass(constraints):
    """
    All the phrase forms enumerated in the Phase 2.5 request must pass when they
    appear under a Forbidden / Prohibited / Not Allowed heading.
    """
    for heading in ("## Forbidden Shortcuts", "## Forbidden", "## Prohibited", "## Not Allowed"):
        arch = f"""# Architecture

## Stack
React + Vite.

{heading}
- Adding backend services.
- Creating API routes.
- Using PostgreSQL.
- Setting up Flask or Express.
- Introducing any database connections or API calls.
- Implementing user authentication or account features.

## Smoke Checks
- npm run build succeeds
"""
        ok, report = check_requirements_consistency(constraints, {"ARCHITECTURE.md": arch})
        assert ok is True, (
            f"Expected lines under {heading!r} to pass, got:\n{report}"
        )
    print("PASS: Forbidden / Prohibited / Not Allowed section variants all pass")


def test_same_phrases_under_implementation_section_still_fail(constraints):
    """
    The same gerund-form phrases must still be flagged when they appear under
    a real implementation section (## Stack, ## Backend, ## Database, ## API)
    where they are genuinely build instructions rather than prohibitions.
    """
    bad_cases = [
        ("## Stack", "Create API routes using Express."),
        ("## Stack", "Use PostgreSQL for the database."),
        ("## Backend", "Set up authentication with JWT."),
        ("## API", "Build backend/app.py with Flask."),
        ("## Database", "Create a PostgreSQL database with a moods table."),
    ]
    for heading, bad_line in bad_cases:
        arch = f"""# Architecture

{heading}
{bad_line}

## Forbidden Shortcuts
- None.

## Smoke Checks
- npm run build succeeds

## Deployment Assumptions
Local only, macOS.
"""
        ok, report = check_requirements_consistency(constraints, {"ARCHITECTURE.md": arch})
        assert ok is False, (
            f"Expected implementation line to fail under {heading!r}: {bad_line!r}\n{report}"
        )
    print("PASS: implementation section lines with forbidden terms are still caught")


if __name__ == "__main__":
    constraints = test_constraints_detected()
    test_bad_artifacts_are_caught(constraints)
    test_good_artifacts_pass(constraints)
    test_run_017_no_inclusion_phrase_passes(constraints)
    test_prohibition_phrasing(constraints)
    test_run_016_out_of_scope_lines_pass(constraints)
    test_safe_exclusion_examples_pass(constraints)
    test_blocked_instruction_examples_still_fail(constraints)
    test_express_word_vs_express_framework(constraints)
    test_bad_spec_with_psql_is_blocked(constraints)
    test_safe_negated_phrases_pass(constraints)
    test_frontend_only_smoke_checks_doc_has_no_backend_boilerplate(constraints)
    test_run_019_forbidden_shortcuts_section_passes(constraints)
    test_forbidden_section_variants_all_pass(constraints)
    test_same_phrases_under_implementation_section_still_fail(constraints)
    print("\nALL TESTS PASSED")
