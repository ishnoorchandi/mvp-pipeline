"""
Deterministic tests for sprint decomposition:
  1. sprint plan JSON parser can select Sprint 1
  2. selected sprint build prompt includes "Build only Sprint 1."
  3. selected sprint build prompt does not include instructions to build future sprints
  4. --sprint-plan-only stops before Claude Code
  5. sprint mode still respects negative constraints

No OpenAI / DeepSeek calls — generate_sprint_plan() (the only GPT-calling function in
this feature) is never invoked here. Everything tested is pure parsing/template logic.

Run with:
    ./venv/bin/python3 tests/test_sprint_decomposition.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_mvp_builder import (
    parse_sprint_plan_json,
    normalize_sprint_plan,
    select_sprint,
    apply_selected_sprint,
    SprintNotFoundError,
    SprintPlanParseError,
    render_sprint_plan_markdown,
    render_sprint_plan_terminal,
    generate_selected_sprint_build_prompt,
    detect_negative_constraints,
    check_requirements_consistency,
    _is_plan_only_run,
    SPRINT_PLAN_SYSTEM,
)


# ── Shared fixture: a 3-sprint plan for a frontend-only mood picker ───────────

RAW_SPRINT_PLAN_JSON = """```json
{
  "product_name": "Mood Picker",
  "total_sprints": 3,
  "sprints": [
    {
      "number": 1,
      "title": "Core mood selection UI",
      "goal": "Show five mood cards and let the user select one.",
      "why_this_order": "This is the smallest possible end-to-end demoable slice.",
      "files_modules_touched": ["src/App.tsx", "src/MoodCard.tsx"],
      "user_visible_result": "User sees five mood cards, clicks one, selected mood is displayed.",
      "smoke_checks": ["npm run build succeeds", "clicking a card displays the mood"],
      "dependencies": [],
      "independently_demoable": true,
      "build_now": true
    },
    {
      "number": 2,
      "title": "Mood history (local only)",
      "goal": "Remember previously selected moods in-session.",
      "why_this_order": "Builds on Sprint 1's selection mechanism.",
      "files_modules_touched": ["src/MoodHistory.tsx"],
      "user_visible_result": "A list of previously picked moods is shown below the cards.",
      "smoke_checks": ["selecting multiple moods adds to history list"],
      "dependencies": [1],
      "independently_demoable": true,
      "build_now": false
    },
    {
      "number": 3,
      "title": "Theming and animations",
      "goal": "Add color themes per mood and selection animation.",
      "why_this_order": "Polish layer, not needed for core functionality.",
      "files_modules_touched": ["src/theme.ts"],
      "user_visible_result": "Selecting a mood changes the background theme with animation.",
      "smoke_checks": ["theme changes on selection"],
      "dependencies": [1, 2],
      "independently_demoable": false,
      "build_now": false
    }
  ]
}
```"""

CLEAN_REQUIREMENTS = (
    "# Clean Requirements: Mood Picker\n\n"
    "1. Display five mood cards.\n"
    "2. Frontend-only — no backend, no database, no login.\n"
)
MVP_SPEC = "# MVP Spec\nA frontend-only mood picker app."
ARCHITECTURE_TEXT = "# Architecture\nReact + Vite, frontend-only. No backend, no database."

FRONTEND_CONSTRAINTS = detect_negative_constraints(
    "Build a frontend-only mood picker app. No backend, no database, no login.",
    "",
    CLEAN_REQUIREMENTS,
)


# ── Shared fixture: an 8-sprint plan for a genuinely complex product ──────────
# (AI code review platform: roles, backend, database, auth, AI review loop,
# dashboard, integrations, deployment — should NOT be compressed into 2-3 sprints.)

def _complex_sprint_entry(number, title, deps, build_now=False):
    return {
        "number": number,
        "title": title,
        "goal": f"Goal for sprint {number}.",
        "why_this_order": f"Why sprint {number} comes here.",
        "files_modules_touched": [f"src/sprint{number}.ts"],
        "user_visible_result": f"User-visible result for sprint {number}.",
        "smoke_checks": [f"smoke check for sprint {number}"],
        "dependencies": deps,
        "independently_demoable": True,
        "build_now": build_now,
    }


RAW_COMPLEX_SPRINT_PLAN_JSON = """```json
{
  "product_name": "AI Code Review Platform",
  "complexity_level": "complex",
  "recommended_sprint_count": 8,
  "reason_for_sprint_count": "Multiple workflows, backend persistence, dashboard views, AI review loop, and deployment concerns.",
  "total_sprints": 8,
  "sprints": [
""" + ",\n".join(json.dumps(_complex_sprint_entry(
    n,
    [
        "Core Product Shell", "Primary Submission Workflow", "Backend API + Persistence",
        "Auth + User Roles", "AI Review Loop", "Reviewer Dashboard",
        "Third-Party Integrations", "Deployment + Hardening",
    ][n - 1],
    [] if n == 1 else [n - 1],
    build_now=(n == 1),
)) for n in range(1, 9)) + """
  ]
}
```"""


# ══════════════════════════════════════════════════════════════════════════════
#  1. Sprint plan JSON parser can select Sprint 1
# ══════════════════════════════════════════════════════════════════════════════

def test_parse_sprint_plan_json_basic():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    assert plan["total_sprints"] == 3
    assert len(plan["sprints"]) == 3
    assert [s["number"] for s in plan["sprints"]] == [1, 2, 3]
    print("PASS: parse_sprint_plan_json parses a well-formed fenced JSON plan")


def test_select_sprint_1():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    sprint1 = select_sprint(plan, 1)
    assert sprint1["number"] == 1
    assert sprint1["title"] == "Core mood selection UI"
    print("PASS: select_sprint(plan, 1) returns Sprint 1")


def test_select_sprint_not_found_raises():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    try:
        select_sprint(plan, 99)
        assert False, "Expected SprintNotFoundError for missing sprint number"
    except SprintNotFoundError:
        print("PASS: select_sprint raises SprintNotFoundError for an unknown sprint number")


def test_malformed_json_raises_parse_error():
    try:
        parse_sprint_plan_json("not json at all")
        assert False, "Expected SprintPlanParseError for unparseable text"
    except SprintPlanParseError:
        print("PASS: parse_sprint_plan_json raises SprintPlanParseError for malformed input")


def test_normalize_sprint_plan_fills_defaults():
    normalized = normalize_sprint_plan({"sprints": [{"number": 1}]})
    s = normalized["sprints"][0]
    assert s["title"] == ""
    assert s["files_modules_touched"] == []
    assert s["independently_demoable"] is False
    print("PASS: normalize_sprint_plan fills missing fields with safe defaults")


def test_render_sprint_plan_terminal_marks_selected_build_now():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    terminal = render_sprint_plan_terminal(plan, 1)
    assert "Sprint 1 of 3" in terminal
    assert "Selected Sprint: Sprint 1 of 3" in terminal
    # Sprint 1 selected -> "Build now: yes" must appear; Sprint 2/3 must show "no"
    lines = terminal.split("\n")
    build_now_lines = [l for l in lines if l.startswith("Build now:")]
    assert build_now_lines[0] == "Build now: yes"
    assert build_now_lines[1] == "Build now: no"
    assert build_now_lines[2] == "Build now: no"
    print("PASS: render_sprint_plan_terminal marks only the --selected-sprint as 'Build now: yes'")


# ══════════════════════════════════════════════════════════════════════════════
#  2 & 3. Selected sprint build prompt: builds only Sprint 1, never instructs future builds
# ══════════════════════════════════════════════════════════════════════════════

def test_build_prompt_includes_build_only_sprint_1():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    sprint1 = select_sprint(plan, 1)
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt = generate_selected_sprint_build_prompt(
            CLEAN_REQUIREMENTS, MVP_SPEC, ARCHITECTURE_TEXT,
            plan, sprint1, FRONTEND_CONSTRAINTS, Path(tmpdir),
        )
    assert "Build only Sprint 1." in prompt
    print('PASS: selected sprint build prompt includes "Build only Sprint 1."')


def test_build_prompt_does_not_instruct_building_future_sprints():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    sprint1 = select_sprint(plan, 1)
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt = generate_selected_sprint_build_prompt(
            CLEAN_REQUIREMENTS, MVP_SPEC, ARCHITECTURE_TEXT,
            plan, sprint1, FRONTEND_CONSTRAINTS, Path(tmpdir),
        )
    # Must never tell Claude Code to "Build ... Sprint 2" or "Build ... Sprint 3"
    assert "Build only Sprint 2." not in prompt
    assert "Build only Sprint 3." not in prompt
    assert "Build Sprint 2" not in prompt
    assert "Build Sprint 3" not in prompt
    # Future sprints must be explicitly marked as reference-only / not to be built
    assert "REFERENCE ONLY, DO NOT BUILD" in prompt
    assert "Do not implement Sprint 2 now." in prompt
    assert "Do not implement Sprint 3 now." in prompt
    assert "Do not build Sprint 2 or any sprint after it." in prompt
    print("PASS: selected sprint build prompt never instructs building future sprints")


def test_build_prompt_writes_expected_artifacts():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    sprint1 = select_sprint(plan, 1)
    with tempfile.TemporaryDirectory() as tmpdir:
        rdir = Path(tmpdir)
        generate_selected_sprint_build_prompt(
            CLEAN_REQUIREMENTS, MVP_SPEC, ARCHITECTURE_TEXT,
            plan, sprint1, FRONTEND_CONSTRAINTS, rdir,
        )
        assert (rdir / "selected_sprint_scope.md").exists()
        assert (rdir / "selected_sprint_build_prompt.txt").exists()
        assert (rdir / "sprint_1_scope.md").exists()
        assert (rdir / "sprint_1_build_prompt.txt").exists()
    print("PASS: generate_selected_sprint_build_prompt writes all 4 expected artifact files")


# ══════════════════════════════════════════════════════════════════════════════
#  4. --sprint-plan-only stops before Claude Code
# ══════════════════════════════════════════════════════════════════════════════

def test_sprint_plan_only_triggers_plan_only_gate():
    assert _is_plan_only_run(plan_only=False, sprint_plan_only=True) is True
    print("PASS: --sprint-plan-only alone triggers the plan-only gate")


def test_plan_only_triggers_plan_only_gate():
    assert _is_plan_only_run(plan_only=True, sprint_plan_only=False) is True
    print("PASS: --plan-only alone still triggers the plan-only gate (no regression)")


def test_neither_flag_does_not_trigger_plan_only_gate():
    assert _is_plan_only_run(plan_only=False, sprint_plan_only=False) is False
    print("PASS: normal runs (neither flag set) do not trigger the plan-only gate")


# ══════════════════════════════════════════════════════════════════════════════
#  5. Sprint mode still respects negative constraints
# ══════════════════════════════════════════════════════════════════════════════

def test_sprint_build_prompt_includes_constraint_text():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    sprint1 = select_sprint(plan, 1)
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt = generate_selected_sprint_build_prompt(
            CLEAN_REQUIREMENTS, MVP_SPEC, ARCHITECTURE_TEXT,
            plan, sprint1, FRONTEND_CONSTRAINTS, Path(tmpdir),
        )
    # The no-backend / no-database / no-login constraints must show up in the prompt
    lowered = prompt.lower()
    assert "no backend" in lowered or "frontend-only" in lowered or "frontend only" in lowered
    print("PASS: selected sprint build prompt carries forward the negative constraints text")


def test_sprint_build_prompt_violating_constraint_caught_by_consistency_check():
    """
    If a (hypothetical) selected sprint build prompt violated the 'no backend' constraint,
    the existing check_requirements_consistency() must still catch it when the prompt is
    included in the artifacts dict — exactly as the pipeline does in sprint mode.
    """
    bad_prompt = "Build only Sprint 1.\n\nCreate API routes using Express for mood storage."
    ok, report = check_requirements_consistency(
        FRONTEND_CONSTRAINTS, {"selected_sprint_build_prompt.txt": bad_prompt},
    )
    assert ok is False, f"Expected violation to be caught, got:\n{report}"
    print("PASS: consistency check still catches constraint violations inside a sprint build prompt")


def test_sprint_build_prompt_hard_rules_forbid_backend_unless_required():
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    sprint1 = select_sprint(plan, 1)
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt = generate_selected_sprint_build_prompt(
            CLEAN_REQUIREMENTS, MVP_SPEC, ARCHITECTURE_TEXT,
            plan, sprint1, FRONTEND_CONSTRAINTS, Path(tmpdir),
        )
    assert "Do not add a backend, database, login, or API" in prompt
    print("PASS: selected sprint build prompt's hard rules forbid backend/db/login/API by default")


# ══════════════════════════════════════════════════════════════════════════════
#  Regression: guardrail lines in selected_sprint_build_prompt.txt must NOT trip
#  the consistency checker, but real implementation instructions still must.
# ══════════════════════════════════════════════════════════════════════════════

def test_selected_sprint_guardrail_prompt_passes_consistency_check():
    """
    The full, real generate_selected_sprint_build_prompt() output for a frontend-only
    app — including the _constraints_to_prompt_text() guardrail trailer ("conflicts
    with these constraints... constraints take precedence") and the FRONTEND-ONLY /
    "Do not invent a backend..." directive — must pass check_requirements_consistency()
    under no_backend / no_database / no_api. These are guardrails telling Claude Code
    NOT to build the forbidden thing, not instructions to build it.
    """
    plan = parse_sprint_plan_json(RAW_SPRINT_PLAN_JSON)
    sprint1 = select_sprint(plan, 1)
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt = generate_selected_sprint_build_prompt(
            CLEAN_REQUIREMENTS, MVP_SPEC, ARCHITECTURE_TEXT,
            plan, sprint1, FRONTEND_CONSTRAINTS, Path(tmpdir),
        )
    ok, report = check_requirements_consistency(
        FRONTEND_CONSTRAINTS, {"selected_sprint_build_prompt.txt": prompt},
    )
    assert ok is True, f"Expected guardrail-only prompt to pass consistency check, got:\n{report}"
    print("PASS: real selected sprint build prompt (guardrails only) passes consistency check")


def test_guardrail_phrasing_variants_pass_consistency_check():
    """Each guardrail phrasing the user explicitly called out must pass on its own."""
    guardrail_lines = [
        "If the spec above mentions a Backend/API or Database section that conflicts with "
        "these constraints, ignore those parts of the spec — these constraints take precedence.",
        "This MVP is FRONTEND-ONLY. Do not invent a backend, server, or any backend framework "
        "(Flask/Express/FastAPI/Django).",
        "Do not add backend, database, auth, login, API routes, or persistence if forbidden.",
        "Ignore backend/API/database sections if they conflict with constraints.",
        "Constraints take precedence.",
    ]
    for line in guardrail_lines:
        ok, report = check_requirements_consistency(
            FRONTEND_CONSTRAINTS, {"selected_sprint_build_prompt.txt": line},
        )
        assert ok is True, f"Expected guardrail line to pass:\n  {line}\nGot:\n{report}"
    print("PASS: all 5 user-specified guardrail phrasing variants pass the consistency check")


def test_real_backend_instruction_with_flask_still_fails():
    ok, report = check_requirements_consistency(
        FRONTEND_CONSTRAINTS, {"selected_sprint_build_prompt.txt": "Build a backend with Flask."},
    )
    assert ok is False, f"Expected 'Build a backend with Flask.' to fail, got:\n{report}"
    print("PASS: 'Build a backend with Flask.' still correctly fails (no_backend)")


def test_real_api_routes_instruction_still_fails():
    ok, report = check_requirements_consistency(
        FRONTEND_CONSTRAINTS, {"selected_sprint_build_prompt.txt": "Create API routes."},
    )
    assert ok is False, f"Expected 'Create API routes.' to fail, got:\n{report}"
    print("PASS: 'Create API routes.' still correctly fails (no_api)")


def test_real_postgresql_instruction_still_fails():
    ok, report = check_requirements_consistency(
        FRONTEND_CONSTRAINTS, {"selected_sprint_build_prompt.txt": "Use PostgreSQL."},
    )
    assert ok is False, f"Expected 'Use PostgreSQL.' to fail, got:\n{report}"
    print("PASS: 'Use PostgreSQL.' still correctly fails (no_database)")


def test_real_fastapi_instruction_still_fails():
    ok, report = check_requirements_consistency(
        FRONTEND_CONSTRAINTS, {"selected_sprint_build_prompt.txt": "Set up FastAPI."},
    )
    assert ok is False, f"Expected 'Set up FastAPI.' to fail, got:\n{report}"
    print("PASS: 'Set up FastAPI.' still correctly fails (no_backend)")


def test_real_login_instruction_still_fails():
    ok, report = check_requirements_consistency(
        FRONTEND_CONSTRAINTS, {"selected_sprint_build_prompt.txt": "Add login."},
    )
    assert ok is False, f"Expected 'Add login.' to fail, got:\n{report}"
    print("PASS: 'Add login.' still correctly fails (no_login)")


# ══════════════════════════════════════════════════════════════════════════════
#  Complexity-aware sprint architect: parser accepts the new top-level metadata,
#  a genuinely complex product parses into 6-12 sprints (not compressed to 2-3),
#  build_now is authoritative for only the selected sprint, and future sprints
#  in a large plan are still never included as build instructions.
# ══════════════════════════════════════════════════════════════════════════════

def test_parser_accepts_complexity_level():
    plan = parse_sprint_plan_json(RAW_COMPLEX_SPRINT_PLAN_JSON)
    assert plan["complexity_level"] == "complex"
    print("PASS: parser accepts top-level complexity_level")


def test_parser_accepts_recommended_sprint_count():
    plan = parse_sprint_plan_json(RAW_COMPLEX_SPRINT_PLAN_JSON)
    assert plan["recommended_sprint_count"] == 8
    print("PASS: parser accepts top-level recommended_sprint_count")


def test_parser_preserves_reason_for_sprint_count():
    plan = parse_sprint_plan_json(RAW_COMPLEX_SPRINT_PLAN_JSON)
    assert plan["reason_for_sprint_count"] == (
        "Multiple workflows, backend persistence, dashboard views, AI review loop, "
        "and deployment concerns."
    )
    print("PASS: parser preserves reason_for_sprint_count")


def test_complex_sample_plan_parses_correctly():
    plan = parse_sprint_plan_json(RAW_COMPLEX_SPRINT_PLAN_JSON)
    assert plan["total_sprints"] == 8
    assert len(plan["sprints"]) == 8
    assert 6 <= len(plan["sprints"]) <= 12
    assert [s["number"] for s in plan["sprints"]] == list(range(1, 9))
    print("PASS: a complex 6-12 sprint sample plan parses correctly (not compressed to 2-3)")


def test_normalize_sprint_plan_defaults_and_clamps_complexity_fields():
    # Missing complexity fields -> safe defaults, not a crash.
    normalized = normalize_sprint_plan({"sprints": [{"number": 1}]})
    assert normalized["complexity_level"] == "moderate"
    assert 2 <= normalized["recommended_sprint_count"] <= 12
    assert normalized["reason_for_sprint_count"] == ""

    # Out-of-range / invalid values -> clamped into [2, 12] and a valid level.
    normalized = normalize_sprint_plan({
        "sprints": [{"number": 1}],
        "complexity_level": "nonsense",
        "recommended_sprint_count": 99,
    })
    assert normalized["complexity_level"] == "moderate"
    assert normalized["recommended_sprint_count"] == 12
    print("PASS: normalize_sprint_plan defaults/clamps complexity metadata into valid ranges")


def test_apply_selected_sprint_only_selected_has_build_now_true():
    plan = parse_sprint_plan_json(RAW_COMPLEX_SPRINT_PLAN_JSON)
    selected = apply_selected_sprint(plan, 3)
    assert selected["selected_sprint"] == 3
    build_now_flags = {s["number"]: s["build_now"] for s in selected["sprints"]}
    assert build_now_flags[3] is True
    for n, flag in build_now_flags.items():
        if n != 3:
            assert flag is False, f"Sprint {n} should not have build_now=True when Sprint 3 is selected"
    print("PASS: apply_selected_sprint makes build_now true only for the selected sprint")


def test_render_sprint_plan_terminal_shows_complexity_metadata():
    plan = parse_sprint_plan_json(RAW_COMPLEX_SPRINT_PLAN_JSON)
    terminal = render_sprint_plan_terminal(plan, 1)
    assert "Complexity: complex" in terminal
    assert "Recommended sprint count: 8" in terminal
    assert "Reason: Multiple workflows" in terminal
    print("PASS: terminal sprint plan output shows complexity level, recommended count, and reason")


# ══════════════════════════════════════════════════════════════════════════════
#  Sprint 1 quality: the architect's system prompt must steer Sprint 1 toward a
#  visually demoable shell/dashboard slice for non-trivial products, not a bare
#  setup/schema/backend/form task. Pure prompt-text regression checks — no GPT
#  call — so a future edit can't silently delete this guidance.
# ══════════════════════════════════════════════════════════════════════════════

def test_sprint_plan_prompt_steers_sprint_1_toward_visual_shell():
    prompt = SPRINT_PLAN_SYSTEM
    assert "frontend shell / dashboard / mock-data slice" in prompt
    assert "navigation/sidebar/header" in prompt
    assert "mock data" in prompt
    print("PASS: sprint architect prompt steers Sprint 1 toward a visual shell/dashboard slice")


def test_sprint_plan_prompt_discourages_narrow_sprint_1():
    prompt = SPRINT_PLAN_SYSTEM
    assert "should NOT be only a setup task" in prompt
    assert "only a single plain form" in prompt
    assert '"simple"' in prompt
    print("PASS: sprint architect prompt discourages a bare setup/schema/form-only Sprint 1")


def test_sprint_plan_prompt_includes_recruiting_workspace_example():
    prompt = SPRINT_PLAN_SYSTEM
    assert "Basic Candidate Entry Form" in prompt
    assert "Recruiter Workspace Shell + Candidate Dashboard Mock" in prompt
    print("PASS: sprint architect prompt includes the weak-vs-good Sprint 1 recruiting example")


def test_future_sprints_not_included_as_build_instructions_in_complex_plan():
    plan = parse_sprint_plan_json(RAW_COMPLEX_SPRINT_PLAN_JSON)
    selected = apply_selected_sprint(plan, 1)
    sprint1 = select_sprint(selected, 1)
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt = generate_selected_sprint_build_prompt(
            CLEAN_REQUIREMENTS, MVP_SPEC, ARCHITECTURE_TEXT,
            selected, sprint1, FRONTEND_CONSTRAINTS, Path(tmpdir),
        )
    for n in range(2, 9):
        assert f"Build only Sprint {n}." not in prompt
        assert f"Build Sprint {n}" not in prompt
        assert f"Do not implement Sprint {n} now." in prompt
    assert "REFERENCE ONLY, DO NOT BUILD" in prompt
    print("PASS: in an 8-sprint complex plan, sprints 2-8 are reference-only, never build instructions")


if __name__ == "__main__":
    # 1. Sprint plan JSON parser can select Sprint 1
    test_parse_sprint_plan_json_basic()
    test_select_sprint_1()
    test_select_sprint_not_found_raises()
    test_malformed_json_raises_parse_error()
    test_normalize_sprint_plan_fills_defaults()
    test_render_sprint_plan_terminal_marks_selected_build_now()

    # 2 & 3. Build-only-Sprint-1 / no future-sprint build instructions
    test_build_prompt_includes_build_only_sprint_1()
    test_build_prompt_does_not_instruct_building_future_sprints()
    test_build_prompt_writes_expected_artifacts()

    # 4. --sprint-plan-only stops before Claude Code
    test_sprint_plan_only_triggers_plan_only_gate()
    test_plan_only_triggers_plan_only_gate()
    test_neither_flag_does_not_trigger_plan_only_gate()

    # 5. Sprint mode still respects negative constraints
    test_sprint_build_prompt_includes_constraint_text()
    test_sprint_build_prompt_violating_constraint_caught_by_consistency_check()
    test_sprint_build_prompt_hard_rules_forbid_backend_unless_required()

    # Regression: guardrail lines pass, real instructions still fail
    test_selected_sprint_guardrail_prompt_passes_consistency_check()
    test_guardrail_phrasing_variants_pass_consistency_check()
    test_real_backend_instruction_with_flask_still_fails()
    test_real_api_routes_instruction_still_fails()
    test_real_postgresql_instruction_still_fails()
    test_real_fastapi_instruction_still_fails()
    test_real_login_instruction_still_fails()

    # Complexity-aware sprint architect: metadata parsing, complex-plan sizing,
    # authoritative build_now, and future-sprint safety at larger sprint counts
    test_parser_accepts_complexity_level()
    test_parser_accepts_recommended_sprint_count()
    test_parser_preserves_reason_for_sprint_count()
    test_complex_sample_plan_parses_correctly()
    test_normalize_sprint_plan_defaults_and_clamps_complexity_fields()
    test_apply_selected_sprint_only_selected_has_build_now_true()
    test_render_sprint_plan_terminal_shows_complexity_metadata()
    test_future_sprints_not_included_as_build_instructions_in_complex_plan()

    # Sprint 1 quality: prompt steers toward a visual shell, not a narrow form/setup task
    test_sprint_plan_prompt_steers_sprint_1_toward_visual_shell()
    test_sprint_plan_prompt_discourages_narrow_sprint_1()
    test_sprint_plan_prompt_includes_recruiting_workspace_example()

    print("\nALL TESTS PASSED")
