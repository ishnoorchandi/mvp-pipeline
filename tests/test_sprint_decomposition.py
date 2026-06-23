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
    build_requirement_coverage_map,
    reconcile_sprint_requirement_ids,
    reconcile_sprint_requirement_coverage,
    repair_missing_sprint_requirement_coverage,
    render_requirement_coverage_map_markdown,
    render_sprint_coverage_check,
    score_requirement_sprint_match,
    _extract_source_jira_requirements,
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


def test_requirement_coverage_map_preserves_jira_ids_and_repairs_missing():
    source = (
        "# Dashboard\nATS-29 — KPI Summary Cards\nATS-30 — Approval Queue\n"
        "# Progress\nATS-39 — NDA Signature Tracking\n"
    )
    plan = normalize_sprint_plan({"sprints": [
        {"number": 1, "requirements_covered": [
            {"id": "ATS-29", "title": "KPI Summary Cards"},
            {"id": "ATS-30", "title": "Approval Queue"},
        ]},
    ]})
    coverage = build_requirement_coverage_map(source, plan)
    by_id = {item["id"]: item for item in coverage["coverage_items"]}
    assert by_id["ATS-29"]["covered_by_sprints"] == [1]
    assert by_id["ATS-39"]["coverage_status"] == "covered"
    markdown = render_requirement_coverage_map_markdown(coverage)
    assert "| ATS-29 | KPI Summary Cards | Dashboard | Sprint 1 | covered |" in markdown
    repaired_plan = repair_missing_sprint_requirement_coverage(
        source, reconcile_sprint_requirement_coverage(source, plan)
    )
    ok, report = render_sprint_coverage_check(source, repaired_plan)
    assert ok is True
    assert "Result: PASS" in report and "Missing IDs: none" in report
    print("PASS: coverage map preserves Jira IDs and deterministically repairs omissions")


def test_model_renumbered_ids_are_restored_to_source_ids_and_pass_coverage():
    source = (
        "# OneATS Stories\n"
        "ATS-29 — Dashboard KPI Cards\n"
        "ATS-30 — Approval Queue\n"
        "ATS-31 — Job Requisitions\n"
    )
    model_plan = normalize_sprint_plan({"sprints": [
        {
            "number": 1,
            "requirements_covered": [
                {"id": "ATS-1", "title": "Dashboard KPI Cards"},
                {"id": "ATS-2", "title": "Approval Queue"},
            ],
            "build_items": ["KPI cards", "Approval queue"],
            "not_included": ["Requisition workflow"],
            "completion_criteria": ["Dashboard stories render"],
        },
        {
            "number": 2,
            "requirements_covered": [
                {"id": "ATS-3", "title": "Job Requisitions"},
            ],
        },
    ]})

    reconciled = reconcile_sprint_requirement_ids(source, model_plan)
    reconciled_ids = [
        requirement["id"]
        for sprint in reconciled["sprints"]
        for requirement in sprint["requirements_covered"]
    ]
    assert reconciled_ids == ["ATS-29", "ATS-30", "ATS-31"]

    plan_json = json.dumps(reconciled)
    plan_md = render_sprint_plan_markdown(reconciled)
    coverage = build_requirement_coverage_map(source, reconciled)
    coverage_json = json.dumps(coverage)
    coverage_md = render_requirement_coverage_map_markdown(coverage)
    for requirement_id in ("ATS-29", "ATS-30", "ATS-31"):
        assert requirement_id in plan_json
        assert requirement_id in plan_md
        assert requirement_id in coverage_json
        assert requirement_id in coverage_md
    for invented_id in ("ATS-1", "ATS-2", "ATS-3"):
        assert f'"{invented_id}"' not in plan_json
        assert f"| {invented_id} |" not in coverage_md

    ok, report = render_sprint_coverage_check(source, reconciled)
    assert ok is True
    assert "Covered IDs: ATS-29, ATS-30, ATS-31" in report
    assert "Missing IDs: none" in report
    assert "Result: PASS" in report
    print("PASS: model-renumbered ATS-1/2/3 IDs are restored to ATS-29/30/31 everywhere")


def test_detailed_jira_blocks_override_header_ranges_and_attach_exact_titles():
    source = """OneATS User Stories
Stories: ATS-29 to ATS-44
Generated on June 1, 2026
ATS-44 | Appearance summary row from page header

1. Dashboard KPI Summary Cards
Jira: ATS-29 | Feature: Dashboard | Role: Admin
As an Admin, I want KPI cards so that I can see recruiting activity.
Acceptance Criteria:
- Four KPI cards are visible.

2. Approval Queue with Inline Actions
Jira: ATS-30 | Feature: Dashboard | Role: Admin
As an Admin, I want inline approval actions.
Acceptance Criteria:
- Approve and reject actions update the row.

14. User & Role Management – View, Edit, and Invite Users
Jira: ATS-42 | Feature: Users & Roles | Role: Admin
As an Admin, I want to manage users and roles.
Acceptance Criteria:
- Users can be invited.

15. Account Profile Management
Jira: ATS-43 | Feature: Settings | Role: User
As a User, I want to update my account profile.
Acceptance Criteria:
- Profile changes can be saved.

16. Appearance / Dark Mode / Accent Themes
Jira: ATS-44 | Feature: Settings | Role: User
As a User, I want to configure appearance.
Acceptance Criteria:
- Dark mode and accent themes can be selected.
"""
    extracted = _extract_source_jira_requirements(source)
    assert [item["id"] for item in extracted] == ["ATS-29", "ATS-30", "ATS-42", "ATS-43", "ATS-44"]
    titles = {item["id"]: item["title"] for item in extracted}
    assert titles == {
        "ATS-29": "Dashboard KPI Summary Cards",
        "ATS-30": "Approval Queue with Inline Actions",
        "ATS-42": "User & Role Management – View, Edit, and Invite Users",
        "ATS-43": "Account Profile Management",
        "ATS-44": "Appearance / Dark Mode / Accent Themes",
    }
    assert extracted[-1]["title"] != "Appearance summary row from page header"
    assert extracted[2]["source_area"] == "Users & Roles"
    assert extracted[2]["role"] == "Admin"
    assert extracted[2]["story_text"].startswith("As an Admin")
    assert extracted[2]["acceptance_criteria"] == ["Users can be invited."]

    plan = normalize_sprint_plan({"sprints": [{
        "number": 1,
        "requirements_covered": [
            {"id": item["id"], "title": item["title"]} for item in extracted
        ],
    }]})
    coverage = build_requirement_coverage_map(source, plan)
    assert coverage["coverage_summary"] == {
        "total_items": 5, "covered_items": 5, "uncovered_items": 0, "deferred_items": 0,
    }
    ok, report = render_sprint_coverage_check(source, plan)
    assert ok is True
    assert "Covered IDs: ATS-29, ATS-30, ATS-42, ATS-43, ATS-44" in report
    assert "Missing IDs: none" in report
    assert "Result: PASS" in report
    print("PASS: detailed Jira blocks override range/header metadata and preserve exact story titles")


def test_semantic_reconciliation_repairs_shifted_missing_and_deferred_coverage():
    source = """1. Job Requisition – Create, View, and Search Requisitions
Jira: ATS-31 | Feature: Requisitions | Role: Admin
As an Admin, I want to create, view, and search requisitions.
Acceptance Criteria:
- A requisition modal and searchable requisition list are available.

2. AI Requisition Auto-Approval
Jira: ATS-32 | Feature: Requisitions | Role: Admin
As an Admin, I want AI review and automatic requisition approval.
Acceptance Criteria:
- Requisitions can be auto-approved.

3. AI Resume Matching
Jira: ATS-33 | Feature: Candidates | Role: Recruiter
As a Recruiter, I want ranked candidates and match scores.
Acceptance Criteria:
- Resume matching displays a match score.

4. Interview & Progress Tracking
Jira: ATS-37 | Feature: Progress | Role: Recruiter
As a Recruiter, I want interview schedules and progress tracking.
Acceptance Criteria:
- Interview tracking updates candidate progress.

5. Offer Approval Workflow
Jira: ATS-38 | Feature: Approvals | Role: Admin
As an Admin, I want to approve offers or send them back.
Acceptance Criteria:
- Offer approval actions are available.

6. Settings – Appearance: Dark Mode and Accent Colour Themes
Jira: ATS-44 | Feature: Settings | Role: User
As a User, I want appearance and theme customization.
Acceptance Criteria:
- Dark mode and accent colour themes can be selected.
"""
    bad_plan = normalize_sprint_plan({
        "deferred_requirements": [
            {"id": "ATS-31", "title": "Job Requisition", "reason": "Later"},
            {"id": "ATS-37", "title": "Interview Tracking", "reason": "Later"},
            {"id": "ATS-44", "title": "Appearance", "reason": "Later"},
        ],
        "sprints": [
            {"number": 1, "title": "Requisition Workspace", "goal": "Create, view, and search job requisitions.",
             "requirements_covered": [{"id": "ATS-32", "title": "AI Requisition Auto-Approval"}],
             "build_items": ["Requisition creation modal", "Searchable requisition list"]},
            {"number": 2, "title": "AI Requisition Review", "goal": "Add AI auto-approval for requisitions.",
             "requirements_covered": [{"id": "ATS-33", "title": "AI Resume Matching"}],
             "build_items": ["AI review", "Auto-approved requisition state"]},
            {"number": 3, "title": "Resume Matching", "goal": "Rank candidates using AI resume matching.",
             "requirements_covered": [], "build_items": ["Ranked candidates", "Match score"]},
            {"number": 4, "title": "Interview Tracking", "goal": "Track interview schedules and candidate progress.",
             "requirements_covered": [{"id": "ATS-38", "title": "Offer Approval Workflow"}],
             "build_items": ["Interview schedule", "Progress tracking"]},
            {"number": 5, "title": "Offer Approval", "goal": "Approve offers or send offers back.",
             "requirements_covered": [], "build_items": ["Offer approval actions"]},
            {"number": 6, "title": "Appearance Customization", "goal": "Add dark mode and accent color themes.",
             "requirements_covered": [], "build_items": ["Theme customization"]},
        ],
    })

    repaired = reconcile_sprint_requirement_ids(source, bad_plan)
    repaired = reconcile_sprint_requirement_coverage(source, repaired)
    ids_by_sprint = {
        sprint["number"]: [item["id"] for item in sprint["requirements_covered"]]
        for sprint in repaired["sprints"]
    }
    assert ids_by_sprint == {
        1: ["ATS-31"], 2: ["ATS-32"], 3: ["ATS-33"],
        4: ["ATS-37"], 5: ["ATS-38"], 6: ["ATS-44"],
    }
    assert repaired["deferred_requirements"] == []

    coverage = build_requirement_coverage_map(source, repaired)
    assert coverage["coverage_summary"]["covered_items"] == 6
    assert coverage["coverage_summary"]["uncovered_items"] == 0
    ok, report = render_sprint_coverage_check(source, repaired)
    assert ok is True
    assert "Missing IDs: none" in report
    assert "Result: PASS" in report
    print("PASS: semantic reconciliation repairs shifted, missing, and incorrectly deferred Jira coverage")


def test_missing_requirement_repair_attaches_or_creates_sprints_and_passes_coverage():
    source = """1. Candidate Submissions Tracker
Jira: ATS-35 | Feature: Candidates | Role: Recruiter
As a Recruiter, I want to track all candidate submissions.
Acceptance Criteria:
- All submissions are visible across recruiters.

2. Interview & Progress Tracking
Jira: ATS-37 | Feature: Progress | Role: Recruiter
As a Recruiter, I want interview schedules and progress tracking.
Acceptance Criteria:
- Interview status updates candidate progress.

3. Offer Approval Workflow
Jira: ATS-38 | Feature: Approvals | Role: Admin
As an Admin, I want to approve or send back offers.
Acceptance Criteria:
- Offer approval actions update status.

4. Placement Approval and Automatic Document Handoff
Jira: ATS-40 | Feature: Placements | Role: Admin
As an Admin, I want placement approval and document handoff.
Acceptance Criteria:
- Approved placements automatically hand off documents.

5. Candidate Document Management
Jira: ATS-41 | Feature: Documents | Role: Recruiter
As a Recruiter, I want to attach required candidate documents.
Acceptance Criteria:
- Required candidate documents can be attached and reviewed.
"""
    bad_plan = normalize_sprint_plan({"sprints": [
        {"number": 1, "title": "Candidate Management", "goal": "Add and view candidate profiles.",
         "requirements_covered": [], "build_items": ["Candidate profile workspace"]},
        {"number": 2, "title": "Duplicate Candidate Detection", "goal": "Detect and merge duplicate candidates.",
         "requirements_covered": [], "build_items": ["Duplicate detection", "Merge candidates"]},
        {"number": 3, "title": "User Management", "goal": "Invite users and manage roles.",
         "requirements_covered": [], "build_items": ["User invitations", "Role management"]},
        {"number": 4, "title": "Settings", "goal": "Manage profile and appearance settings.",
         "requirements_covered": [], "build_items": ["Account profile", "Dark mode"]},
    ]})

    repaired = repair_missing_sprint_requirement_coverage(source, bad_plan)
    all_ids = [
        requirement["id"]
        for sprint in repaired["sprints"]
        for requirement in sprint["requirements_covered"]
        if isinstance(requirement, dict)
    ]
    assert set(all_ids) == {"ATS-35", "ATS-37", "ATS-38", "ATS-40", "ATS-41"}
    assert repaired["total_sprints"] == len(repaired["sprints"])
    assert repaired["recommended_sprint_count"] == len(repaired["sprints"])
    assert [sprint["number"] for sprint in repaired["sprints"]] == list(range(1, len(repaired["sprints"]) + 1))

    for requirement_id in all_ids:
        sprint = next(
            sprint for sprint in repaired["sprints"]
            if any(isinstance(item, dict) and item.get("id") == requirement_id for item in sprint["requirements_covered"])
        )
        assert sprint["build_items"], f"{requirement_id} repair must add a build item"
        assert sprint["completion_criteria"], f"{requirement_id} repair must add completion criteria"

    plan_md = render_sprint_plan_markdown(repaired)
    for requirement_id in ("ATS-35", "ATS-37", "ATS-38", "ATS-40", "ATS-41"):
        assert requirement_id in plan_md
    coverage = build_requirement_coverage_map(source, repaired)
    ok, report = render_sprint_coverage_check(source, repaired)
    assert ok is True
    assert coverage["coverage_summary"]["uncovered_items"] == 0
    assert "Missing IDs: none" in report
    assert "Result: PASS" in report
    print("PASS: omitted source stories attach/create concrete sprints and produce PASS coverage")


def test_new_sprint_fields_are_normalized_and_rendered():
    plan = normalize_sprint_plan({"sprints": [{
        "number": 1,
        "title": "Dashboard Shell",
        "requirements_covered": [{"id": "ATS-29", "title": "KPI Cards"}],
        "build_items": ["Four KPI cards", "Approval queue"],
        "not_included": ["Persistence"],
        "completion_criteria": ["All KPI cards render"],
    }]})
    sprint = plan["sprints"][0]
    assert sprint["requirements_covered"][0]["id"] == "ATS-29"
    markdown = render_sprint_plan_markdown(plan)
    assert "**Requirements covered:**" in markdown
    assert "- ATS-29 — KPI Cards" in markdown
    assert "**What will be built:**" in markdown
    assert "**Completion criteria:**" in markdown
    print("PASS: new sprint specificity fields normalize and render without removing old fields")


# ══════════════════════════════════════════════════════════════════════════════
#  Semantic sprint-assignment quality: a passing coverage check must mean the
#  requirement is actually attached to the sprint that builds it, not just that
#  the ID string appears somewhere in the plan. Regressions for the OneATS bug
#  report: ATS-44 (Appearance) dumped into a Job Requisition sprint, ATS-32/33
#  (AI features) dumped into basic requisition CRUD, omitted AI/placement
#  stories not attached/created correctly.
# ══════════════════════════════════════════════════════════════════════════════

def test_appearance_story_misattached_to_requisition_sprint_is_moved():
    """ATS-44 (Appearance) incorrectly placed under a Job Requisition sprint must be
    moved to the Settings/Appearance sprint that actually builds it."""
    source = """1. Job Requisition – Create, View, and Search Requisitions
Jira: ATS-31 | Feature: Requisitions | Role: Admin
As an Admin, I want to create, view, and search job requisitions.
Acceptance Criteria:
- A requisition modal and searchable requisition list are available.

2. Appearance / Dark Mode / Accent Themes
Jira: ATS-44 | Feature: Settings | Role: User
As a User, I want to configure appearance and dark mode.
Acceptance Criteria:
- Dark mode and accent colour themes can be selected.
"""
    bad_plan = normalize_sprint_plan({"sprints": [
        {"number": 2, "title": "Job Requisition Management",
         "goal": "Create, view, and search job requisitions.",
         "requirements_covered": [
             {"id": "ATS-31", "title": "Job Requisition"},
             {"id": "ATS-44", "title": "Appearance"},
         ],
         "build_items": ["Requisition creation modal", "Searchable requisition list"]},
        {"number": 6, "title": "Settings: Appearance",
         "goal": "Let users choose dark mode and accent colour themes.",
         "requirements_covered": [], "build_items": ["Dark mode toggle", "Accent theme picker"]},
    ]})

    reconciled = reconcile_sprint_requirement_coverage(source, bad_plan)
    ids_by_sprint = {
        sprint["number"]: [item["id"] for item in sprint["requirements_covered"]]
        for sprint in reconciled["sprints"]
    }
    assert ids_by_sprint[2] == ["ATS-31"], f"ATS-44 must no longer be in the requisition sprint: {ids_by_sprint}"
    assert ids_by_sprint[6] == ["ATS-44"], f"ATS-44 must move to the appearance sprint: {ids_by_sprint}"

    ok, report = render_sprint_coverage_check(source, reconciled)
    assert ok is True
    assert "Mismatched IDs: none" in report
    print("PASS: ATS-44 misattached to a Job Requisition sprint is moved to the Settings/Appearance sprint")


def test_omitted_ai_resume_matching_story_attaches_to_its_own_empty_sprint():
    """ATS-33 (AI Resume Matching) omitted from the model plan, but an AI Resume Matching
    sprint with no requirements exists — repair must attach it there, not elsewhere."""
    source = """1. AI Resume Matching
Jira: ATS-33 | Feature: Candidates | Role: Recruiter
As a Recruiter, I want ranked candidates and match scores from AI resume matching.
Acceptance Criteria:
- Resume matching displays a match score and matched skills.
"""
    bad_plan = normalize_sprint_plan({"sprints": [
        {"number": 1, "title": "Candidate Management", "goal": "Add and view candidate profiles.",
         "requirements_covered": [], "build_items": ["Candidate profile workspace"]},
        {"number": 2, "title": "AI Resume Matching", "goal": "Rank candidates using AI resume matching.",
         "requirements_covered": [], "build_items": ["Ranked candidates", "Match score", "Skill gaps"]},
    ]})

    repaired = repair_missing_sprint_requirement_coverage(source, bad_plan)
    ids_by_sprint = {
        sprint["number"]: [item["id"] for item in sprint["requirements_covered"]]
        for sprint in repaired["sprints"]
    }
    assert ids_by_sprint[2] == ["ATS-33"], f"ATS-33 must attach to the AI Resume Matching sprint: {ids_by_sprint}"
    assert ids_by_sprint[1] == [], "ATS-33 must not attach to the unrelated Candidate Management sprint"
    print("PASS: omitted ATS-33 attaches to the existing empty AI Resume Matching sprint")


def test_omitted_placement_story_with_no_matching_sprint_creates_new_sprint():
    """ATS-40 (Placement Approval) omitted with no placement/document sprint anywhere —
    repair must create a new, specific sprint rather than attach it randomly."""
    source = """1. Placement Approval and Automatic Document Handoff
Jira: ATS-40 | Feature: Placements | Role: Admin
As an Admin, I want placement approval and automatic document handoff.
Acceptance Criteria:
- Approved placements automatically hand off documents.
- Pay rate, bill rate, and margin are shown before approval.
"""
    bad_plan = normalize_sprint_plan({"sprints": [
        {"number": 1, "title": "Candidate Management", "goal": "Add and view candidate profiles.",
         "requirements_covered": [], "build_items": ["Candidate profile workspace"]},
        {"number": 2, "title": "Settings: Appearance", "goal": "Dark mode and accent colour themes.",
         "requirements_covered": [], "build_items": ["Theme picker"]},
    ]})

    repaired = repair_missing_sprint_requirement_coverage(source, bad_plan)
    assert len(repaired["sprints"]) == 3, "A dedicated new sprint must be created for ATS-40"
    new_sprint = repaired["sprints"][-1]
    assert any(
        isinstance(item, dict) and item.get("id") == "ATS-40"
        for item in new_sprint["requirements_covered"]
    )
    assert new_sprint["build_items"], "the new sprint must have concrete build items, not be vague"
    assert new_sprint["completion_criteria"], "the new sprint must have concrete completion criteria"
    print("PASS: omitted ATS-40 with no matching sprint creates a new, specific placement sprint")


def test_coverage_check_fails_when_requirement_dumped_into_unrelated_sprint():
    """The coverage check must WARN (not PASS) when a requirement's ID is technically
    present but attached only to a sprint that is demonstrably about something else —
    this is the exact 'fake PASS' bug: dumping missing IDs into unrelated sprints."""
    source = """1. Appearance / Dark Mode / Accent Themes
Jira: ATS-44 | Feature: Settings | Role: User
As a User, I want to configure appearance and dark mode.
Acceptance Criteria:
- Dark mode and accent colour themes can be selected.
"""
    fake_pass_plan = normalize_sprint_plan({"sprints": [
        {"number": 1, "title": "Job Requisition Management",
         "goal": "Create, view, and search job requisitions.",
         "requirements_covered": [{"id": "ATS-44", "title": "Appearance"}],
         "build_items": ["Requisition creation modal", "Searchable requisition list"]},
    ]})
    ok, report = render_sprint_coverage_check(source, fake_pass_plan)
    assert ok is False, f"Expected WARN for a requirement dumped into an unrelated sprint, got:\n{report}"
    assert "Result: WARN" in report
    assert "ATS-44" in report
    print("PASS: coverage check WARNs (does not fake-PASS) when a requirement is dumped into an unrelated sprint")

    deferred_plan = normalize_sprint_plan({
        "deferred_requirements": [{"id": "ATS-44", "title": "Appearance", "reason": "Post-MVP polish."}],
        "sprints": [{"number": 1, "title": "Job Requisition Management", "requirements_covered": []}],
    })
    ok, report = render_sprint_coverage_check(source, deferred_plan)
    assert ok is True, f"Expected PASS for an explicitly deferred requirement, got:\n{report}"
    print("PASS: coverage check PASSes when a requirement is explicitly deferred with a reason")


_ONEATS_FULL_SOURCE = """OneATS User Stories
Stories: ATS-29 to ATS-44

1. Dashboard KPI Summary Cards
Jira: ATS-29 | Feature: Dashboard | Role: Admin
As an Admin, I want KPI summary cards so that I can see recruiting metrics at a glance.
Acceptance Criteria:
- Four KPI cards are visible on the dashboard.

2. Approval Queue with Inline Actions
Jira: ATS-30 | Feature: Dashboard | Role: Admin
As an Admin, I want an approval queue with inline actions on the dashboard.
Acceptance Criteria:
- The approval queue supports inline approve/reject actions.

3. Job Requisition – Create, View, and Search
Jira: ATS-31 | Feature: Requisitions | Role: Admin
As an Admin, I want to create, view, and search job requisitions.
Acceptance Criteria:
- A requisition modal and searchable requisition list are available.
- Rate card and required skills are captured per requisition.

4. AI Requisition Auto-Approval
Jira: ATS-32 | Feature: Requisitions | Role: Admin
As an Admin, I want AI review and auto-approval of requisitions.
Acceptance Criteria:
- Requisitions can be auto-approved or sent to manual review.

5. AI Resume Matching
Jira: ATS-33 | Feature: Candidates | Role: Recruiter
As a Recruiter, I want AI resume matching with ranked candidates and match scores.
Acceptance Criteria:
- Resume matching shows a match score and matched skills per candidate.

6. Candidate Management
Jira: ATS-34 | Feature: Candidates | Role: Recruiter
As a Recruiter, I want to add and view candidate profiles.
Acceptance Criteria:
- A new candidate can be added with a target role and resume upload.

7. Candidate Submissions Tracker
Jira: ATS-35 | Feature: Candidates | Role: Recruiter
As a Recruiter, I want a submissions tracker across all recruiters.
Acceptance Criteria:
- All submissions show a cross-recruiter submission status.

8. Duplicate Candidate Detection
Jira: ATS-36 | Feature: Candidates | Role: Recruiter
As a Recruiter, I want duplicate candidate detection with merge support.
Acceptance Criteria:
- Possible duplicates with the same phone or email can be dismissed or merged.

9. Interview & Progress Tracking
Jira: ATS-37 | Feature: Progress | Role: Recruiter
As a Recruiter, I want interview scheduling and progress tracking.
Acceptance Criteria:
- Interview rounds show scheduled, completed, or feedback pending status.

10. Offer Approval Workflow
Jira: ATS-38 | Feature: Approvals | Role: Admin
As an Admin, I want to approve offers awaiting approval or send them back.
Acceptance Criteria:
- Offers awaiting approval can be approved and sent, or sent back with the offered amount shown.

11. NDA & Document Signature Tracking
Jira: ATS-39 | Feature: Compliance | Role: Admin
As an Admin, I want NDA and signature tracking with reminders.
Acceptance Criteria:
- Signed, expired, and send-reminder states are tracked for each consent form.

12. Placement Approval and Automatic Document Handoff
Jira: ATS-40 | Feature: Placements | Role: Admin
As an Admin, I want placement approval and automatic document handoff.
Acceptance Criteria:
- Placement approval shows pay rate, bill rate, and margin before closure.
- Approved placements automatically hand off documents.

13. Candidate Document Management
Jira: ATS-41 | Feature: Documents | Role: Recruiter
As a Recruiter, I want candidate document management with a checklist.
Acceptance Criteria:
- Required documents like offer letter, background check, and I-9 can be attached.

14. User & Role Management
Jira: ATS-42 | Feature: Users & Roles | Role: Admin
As an Admin, I want to invite users and assign roles.
Acceptance Criteria:
- Users can be invited and have a role assignment with last login shown.

15. Account Profile Management
Jira: ATS-43 | Feature: Settings | Role: User
As a User, I want to update my account profile.
Acceptance Criteria:
- Full name, email, title, and phone can be saved.

16. Appearance / Dark Mode / Accent Themes
Jira: ATS-44 | Feature: Settings | Role: User
As a User, I want to configure appearance, dark mode, and accent colour themes.
Acceptance Criteria:
- Dark mode and accent colour themes (Gold, Steel Blue, Sage, Terracotta, Plum) can be selected.
"""

_ONEATS_BAD_MODEL_PLAN = normalize_sprint_plan({
    "sprints": [
        {"number": 1, "title": "Dashboard Shell", "goal": "Show KPI cards and an approval queue.",
         "requirements_covered": [
             {"id": "ATS-29", "title": "Dashboard KPI Summary Cards"},
             {"id": "ATS-30", "title": "Approval Queue with Inline Actions"},
         ],
         "build_items": ["Four KPI cards", "Approval queue with inline actions"]},
        # Bug-report scenario: AI features and Appearance dumped into requisition CRUD.
        {"number": 2, "title": "Job Requisition Management",
         "goal": "Create, view, and search job requisitions.",
         "requirements_covered": [
             {"id": "ATS-31", "title": "Job Requisition"},
             {"id": "ATS-32", "title": "AI Requisition Auto-Approval"},
             {"id": "ATS-33", "title": "AI Resume Matching"},
             {"id": "ATS-44", "title": "Appearance"},
         ],
         "build_items": ["Requisition creation modal", "Searchable requisition list"]},
        # Bug-report scenario: titled "AI Features" but covers nothing.
        {"number": 3, "title": "Advanced AI Features Integration",
         "goal": "AI-assisted requisition review and resume matching.",
         "requirements_covered": [], "build_items": ["AI review pipeline"]},
        {"number": 4, "title": "Candidate Workspace", "goal": "Add, submit, and de-duplicate candidates.",
         "requirements_covered": [
             {"id": "ATS-34", "title": "Candidate Management"},
             {"id": "ATS-35", "title": "Candidate Submissions Tracker"},
             {"id": "ATS-36", "title": "Duplicate Candidate Detection"},
         ],
         "build_items": ["Candidate profile", "Submissions tracker", "Duplicate detection"]},
        {"number": 5, "title": "Interview, Offers & Compliance",
         "goal": "Track interview progress, offer approval, and NDA signatures.",
         "requirements_covered": [
             {"id": "ATS-37", "title": "Interview & Progress Tracking"},
             {"id": "ATS-38", "title": "Offer Approval Workflow"},
             {"id": "ATS-39", "title": "NDA & Document Signature Tracking"},
         ],
         "build_items": ["Interview progress tracking", "Offer approval workflow", "NDA signature reminders"]},
        {"number": 6, "title": "Placements & Document Handoff",
         "goal": "Approve placements and hand off required documents.",
         "requirements_covered": [
             {"id": "ATS-40", "title": "Placement Approval and Automatic Document Handoff"},
             {"id": "ATS-41", "title": "Candidate Document Management"},
         ],
         "build_items": ["Placement approval", "Document handoff checklist"]},
        {"number": 7, "title": "Settings & Administration",
         "goal": "Manage users, roles, and the account profile.",
         "requirements_covered": [
             {"id": "ATS-42", "title": "User & Role Management"},
             {"id": "ATS-43", "title": "Account Profile Management"},
         ],
         "build_items": ["User invitations", "Role assignment", "Account profile form"]},
    ],
})


def test_oneats_full_fixture_produces_no_missing_and_no_obviously_wrong_mapping():
    """End-to-end OneATS-style fixture (ATS-29 through ATS-44) reproducing the exact bug
    report: ATS-44/ATS-32/ATS-33 dumped into a Job Requisition sprint and an empty 'AI
    Features' sprint. After reconciliation + repair, coverage must PASS with no missing
    IDs and no obviously wrong mapping (ATS-44 must not stay under Job Requisition
    Management; ATS-32/ATS-33 must not stay under basic requisition CRUD)."""
    plan = reconcile_sprint_requirement_ids(_ONEATS_FULL_SOURCE, _ONEATS_BAD_MODEL_PLAN)
    plan = reconcile_sprint_requirement_coverage(_ONEATS_FULL_SOURCE, plan)
    plan = repair_missing_sprint_requirement_coverage(_ONEATS_FULL_SOURCE, plan)

    location_by_id: dict[str, list[str]] = {}
    for sprint in plan["sprints"]:
        for requirement in sprint.get("requirements_covered") or []:
            if isinstance(requirement, dict) and requirement.get("id"):
                location_by_id.setdefault(requirement["id"], []).append(sprint.get("title", ""))

    for expected_id in (f"ATS-{n}" for n in range(29, 45)):
        assert expected_id in location_by_id, f"{expected_id} must be covered by some sprint"

    assert "Job Requisition Management" not in location_by_id["ATS-44"], (
        f"ATS-44 (Appearance) must not stay under Job Requisition Management: {location_by_id['ATS-44']}"
    )
    for ai_id in ("ATS-32", "ATS-33"):
        assert "Job Requisition Management" not in location_by_id[ai_id], (
            f"{ai_id} (AI feature) must not stay under basic requisition CRUD: {location_by_id[ai_id]}"
        )

    coverage_map = build_requirement_coverage_map(_ONEATS_FULL_SOURCE, plan)
    assert coverage_map["coverage_summary"]["uncovered_items"] == 0
    ok, report = render_sprint_coverage_check(_ONEATS_FULL_SOURCE, plan)
    assert ok is True, f"Expected PASS, got:\n{report}"
    assert "Missing IDs: none" in report
    assert "Mismatched IDs: none" in report
    print("PASS: OneATS-style ATS-29..ATS-44 fixture reconciles with no missing IDs and no obviously wrong mapping")


# ══════════════════════════════════════════════════════════════════════════════
#  no_database constraint detection must be phrase-based, not a bare "database"
#  keyword scan. A requirements doc that talks about a "resume database", data
#  "sourced from the database", "Transport Security to the Database", or an
#  "application-to-database connection" is NOT prohibiting a database — those are
#  normal positive database requirements. Only explicit prohibition phrases like
#  "no database" / "without a database" / "must not use database" count.
# ══════════════════════════════════════════════════════════════════════════════

def test_no_database_not_triggered_by_normal_database_mentions():
    non_prohibitions = [
        "database",
        "resume database",
        "candidate database",
        "sourced from the database",
        "database schema",
        "database encryption",
        "database access",
        "database persistence",
        "Transport Security to the Database",
        "secure database connection",
        "application-to-database connection",
        "stored in the database",
        "PostgreSQL",
        "Encrypt the application-to-database connection",
        "Scan every resume in the database",
        "Store candidate records in the database",
    ]
    for text in non_prohibitions:
        constraints = detect_negative_constraints(text)
        assert constraints["no_database"] is False, (
            f"'{text}' must NOT trigger no_database — it is not a prohibition"
        )
    print("PASS: normal positive database mentions never trigger no_database")


def test_no_database_triggered_by_explicit_prohibition_phrases():
    prohibitions = [
        "no database",
        "do not use a database",
        "don't use a database",
        "without a database",
        "without database",
        "no backend database",
        "no database persistence",
        "frontend only, no database",
        "must not use database",
        "avoid database",
        "Do not use a database",
        "Build this without a database",
        "Frontend only, no database",
        "Must not use database",
    ]
    for text in prohibitions:
        constraints = detect_negative_constraints(text)
        assert constraints["no_database"] is True, (
            f"'{text}' must trigger no_database — it is an explicit prohibition"
        )
    print("PASS: explicit no-database prohibition phrases correctly trigger no_database")


def test_database_mention_not_blocked_when_no_database_inactive():
    """If no_database was never detected (e.g. the requirements only mention a normal,
    positive database requirement), the consistency checker must not block a sprint
    prompt just because it mentions 'database'."""
    constraints = detect_negative_constraints(
        "Scan every resume in the database and store candidate records in the database. "
        "Encrypt the application-to-database connection with Transport Security to the Database."
    )
    assert constraints["no_database"] is False
    ok, report = check_requirements_consistency(
        constraints,
        {"selected_sprint_build_prompt.txt": "Use PostgreSQL to store candidate records in the database."},
    )
    assert ok is True, f"Expected no block when no_database is inactive, got:\n{report}"
    print("PASS: mentioning 'database' is not blocked when no_database was never detected")


# ══════════════════════════════════════════════════════════════════════════════
#  Regression: run_064 still falsely detected no_database via the REAL pipeline
#  path (detect_negative_constraints over raw_input + clean_requirements), because
#  of an unrelated risk note — "No DB connection pooling (resource exhaustion
#  risk)" — not a prohibition on using a database at all. Also: "Transport
#  Security to the Database" / "Encrypt the application-to-database connection"
#  must never trigger no_database through this same real detection path.
# ══════════════════════════════════════════════════════════════════════════════

def test_no_database_not_triggered_through_real_pipeline_detection_path():
    raw_input = (
        "OneATS Admin Console — User Stories\n\n"
        "US-3.1 — Automatically score every applicant against the requisition\n"
        "As an admin, I want every resume — whether submitted internally or "
        "sourced from the database — automatically scored.\n"
        "Acceptance criteria: Scan every resume in the database; store candidate records in the database.\n"
        "Jira: ON-49 — US-3.1 — Automatically score every applicant against the requisition\n\n"
        "SR-7 — Transport Security to the Database\n"
        "What it is: Encrypt the application-to-database connection.\n"
        "What it uses: common/db.py with a configurable DB_SSL_MODE.\n"
        "How it's used: Managed/production runtimes default to verify-full.\n\n"
        "IB-5\n"
        "No DB connection pooling (resource exhaustion risk)\n"
    )
    clean_requirements = "Candidate records are sourced from the database and stored in the database."
    constraints = detect_negative_constraints(raw_input, "", clean_requirements)
    assert constraints["no_database"] is False, (
        "Real pipeline detection path must not flag no_database for risk notes or "
        "normal database mentions"
    )
    print("PASS: real pipeline detection path (raw_input + clean_requirements) never falsely triggers no_database")


def test_transport_security_to_database_never_triggers_no_database():
    for text in (
        "Transport Security to the Database",
        "SR-7 — Transport Security to the Database",
        "Encrypt the application-to-database connection.",
        "What it uses: common/db.py with a configurable DB_SSL_MODE: disable, require, verify-full.",
    ):
        constraints = detect_negative_constraints(text)
        assert constraints["no_database"] is False, f"'{text}' must never trigger no_database"
    print("PASS: 'Transport Security to the Database' and related security text never trigger no_database")


# ══════════════════════════════════════════════════════════════════════════════
#  Sprint repair quality: security/infrastructure requirements must be grouped
#  into a dedicated Security/Infrastructure sprint, never dumped into Dashboard
#  Shell just because it's the only sprint that exists. Repair-created sprints
#  must have clean, descriptive titles — never "General: Jira: ...".
# ══════════════════════════════════════════════════════════════════════════════

_SECURITY_REQUIREMENTS_SOURCE = """US-1.1 — View organization-wide performance at a glance
As an admin, I want a dashboard summarizing open requisitions and pending approvals.
Acceptance criteria: Dashboard loads by default on login; each stat tile shows a current value.
Jira: ON-42 — US-1.1 — View organization-wide performance at a glance

SR-1 — Authentication
What it is: Verify caller identity before any protected operation.
What it uses: Bearer JWTs signed with JWT_SECRET; pluggable via AUTH_METHOD.
How it's used: get_current_user() decodes the bearer credential and returns role and permissions.

SR-2 — Password Storage & Verification
What it is: Credentials must never be stored or compared in plaintext.
What it uses: PBKDF2-HMAC-SHA256 with a random salt; bcrypt supported for legacy hashes.
How it's used: hash_password() and verify_password() are called during login.

SR-7 — Transport Security to the Database
What it is: Encrypt the application-to-database connection.
What it uses: A configurable DB_SSL_MODE: disable, require, verify-full.
How it's used: Managed/production runtimes default to verify-full and force SSL on the connection.
"""


def test_security_requirements_grouped_into_dedicated_sprint_not_dashboard_shell():
    bad_plan = normalize_sprint_plan({"sprints": [
        {"number": 1, "title": "Dashboard Shell",
         "goal": "Show KPI summary cards and an approval queue.",
         "requirements_covered": [],
         "build_items": ["KPI summary cards", "Approval queue with inline actions"]},
    ]})
    plan = reconcile_sprint_requirement_ids(_SECURITY_REQUIREMENTS_SOURCE, bad_plan)
    plan = reconcile_sprint_requirement_coverage(_SECURITY_REQUIREMENTS_SOURCE, plan)
    plan = repair_missing_sprint_requirement_coverage(_SECURITY_REQUIREMENTS_SOURCE, plan)

    ids_by_sprint = {
        sprint["number"]: {
            "title": sprint["title"],
            "ids": [r.get("id") for r in sprint.get("requirements_covered") or []],
        }
        for sprint in plan["sprints"]
    }
    dashboard_sprint = ids_by_sprint[1]
    assert dashboard_sprint["title"] == "Dashboard Shell"
    assert dashboard_sprint["ids"] == ["ON-42"], (
        f"Dashboard Shell must contain only the dashboard requirement, got: {dashboard_sprint['ids']}"
    )

    security_sprints = [s for s in ids_by_sprint.values() if {"SR-1", "SR-2", "SR-7"} & set(s["ids"])]
    assert len(security_sprints) == 1, f"Security requirements must land in exactly one sprint: {security_sprints}"
    security_sprint = security_sprints[0]
    assert set(security_sprint["ids"]) == {"SR-1", "SR-2", "SR-7"}
    assert "Dashboard" not in security_sprint["title"]
    assert "Security" in security_sprint["title"]
    print("PASS: security/infrastructure requirements group into a dedicated Security sprint, not Dashboard Shell")


def test_repair_created_sprints_never_titled_general_jira():
    source = """Jira: ON-45 — US-2.2 — Auto-publish new requisitions to Ceipal
US-2.2 — Auto-publish new requisitions to Ceipal
As an admin, I want every newly created requisition automatically posted to Ceipal.
Acceptance criteria: On save, the system calls the Ceipal API to create the requisition.
Jira: ON-45 — US-2.2 — Auto-publish new requisitions to Ceipal
"""
    # A messier, ambiguous source line shaped like the original bug report — a Jira
    # anchor with no clean preceding title at all — must still never produce a
    # "General: Jira: ..." sprint title.
    messy_source = "Jira: ON-45 — US-2.2 — Auto-publish new requisitions to Ceipal\n"
    for text in (source, messy_source):
        plan = normalize_sprint_plan({"sprints": []})
        repaired = repair_missing_sprint_requirement_coverage(text, plan)
        for sprint in repaired["sprints"]:
            assert not sprint["title"].startswith("General: Jira:"), (
                f"Repair-created sprint must never be titled 'General: Jira: ...', got: {sprint['title']!r}"
            )
            assert "Jira:" not in sprint["title"], f"Sprint title leaked raw 'Jira:' text: {sprint['title']!r}"
    print("PASS: repair-created sprints are never titled 'General: Jira: ...'")


def test_coverage_pass_requires_no_infrastructure_dumped_into_ui_shell_sprint():
    """End-to-end quality gate: coverage PASS must require not just zero missing/
    mismatched IDs, but also that no obvious infrastructure requirement ended up in
    an unrelated UI shell sprint (score_requirement_sprint_match would flag the
    domain conflict if it had)."""
    bad_plan = normalize_sprint_plan({"sprints": [
        {"number": 1, "title": "Dashboard Shell",
         "goal": "Show KPI summary cards and an approval queue.",
         "requirements_covered": [],
         "build_items": ["KPI summary cards", "Approval queue with inline actions"]},
    ]})
    plan = reconcile_sprint_requirement_ids(_SECURITY_REQUIREMENTS_SOURCE, bad_plan)
    plan = reconcile_sprint_requirement_coverage(_SECURITY_REQUIREMENTS_SOURCE, plan)
    plan = repair_missing_sprint_requirement_coverage(_SECURITY_REQUIREMENTS_SOURCE, plan)

    coverage = build_requirement_coverage_map(_SECURITY_REQUIREMENTS_SOURCE, plan)
    assert coverage["coverage_summary"]["uncovered_items"] == 0
    ok, report = render_sprint_coverage_check(_SECURITY_REQUIREMENTS_SOURCE, plan)
    assert ok is True, f"Expected PASS, got:\n{report}"
    assert "Missing IDs: none" in report
    assert "Mismatched IDs: none" in report

    dashboard_sprint = next(s for s in plan["sprints"] if s["title"] == "Dashboard Shell")
    dashboard_ids = {r.get("id") for r in dashboard_sprint.get("requirements_covered") or []}
    assert not ({"SR-1", "SR-2", "SR-7"} & dashboard_ids), (
        "Coverage PASS must not be achieved by dumping security requirements into Dashboard Shell"
    )
    print("PASS: coverage PASS requires no infrastructure requirements dumped into the UI shell sprint")


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

    # Requirement coverage and richer sprint schema
    test_requirement_coverage_map_preserves_jira_ids_and_repairs_missing()
    test_model_renumbered_ids_are_restored_to_source_ids_and_pass_coverage()
    test_detailed_jira_blocks_override_header_ranges_and_attach_exact_titles()
    test_semantic_reconciliation_repairs_shifted_missing_and_deferred_coverage()
    test_missing_requirement_repair_attaches_or_creates_sprints_and_passes_coverage()
    test_new_sprint_fields_are_normalized_and_rendered()

    # Semantic sprint-assignment quality (OneATS bug report regressions)
    test_appearance_story_misattached_to_requisition_sprint_is_moved()
    test_omitted_ai_resume_matching_story_attaches_to_its_own_empty_sprint()
    test_omitted_placement_story_with_no_matching_sprint_creates_new_sprint()
    test_coverage_check_fails_when_requirement_dumped_into_unrelated_sprint()
    test_oneats_full_fixture_produces_no_missing_and_no_obviously_wrong_mapping()

    # no_database constraint detection: phrase-based, not a bare keyword scan
    test_no_database_not_triggered_by_normal_database_mentions()
    test_no_database_triggered_by_explicit_prohibition_phrases()
    test_database_mention_not_blocked_when_no_database_inactive()
    test_no_database_not_triggered_through_real_pipeline_detection_path()
    test_transport_security_to_database_never_triggers_no_database()

    # Sprint repair quality: security grouping, no "General: Jira:" fallback titles
    test_security_requirements_grouped_into_dedicated_sprint_not_dashboard_shell()
    test_repair_created_sprints_never_titled_general_jira()
    test_coverage_pass_requires_no_infrastructure_dumped_into_ui_shell_sprint()

    print("\nALL TESTS PASSED")
