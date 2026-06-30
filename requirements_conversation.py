"""
Requirements Conversation — interactive requirements sign-off before architecture/build.

Provides template-based draft generation and structured question templates for three
build-capable entry points:
  raw_idea            → MVP scope questions
  written_requirements → gap-filling questions
  existing_app_upgrade → preserve/reuse/scope/risk questions

Artifact files written/read per run:
  mvp_requirements_draft.md       — draft produced from raw idea / request text
  requirements_questions.json     — structured Q&A state (primary storage)
  requirements_conversation.md    — human-readable transcript
  approved_requirements.md        — final approved requirements
  requirements_signoff_state.json — approval record (detected by planning_gate.py)

Lazy-init: conversation state is created on first access if the artifacts don't
yet exist — existing runs and plan-only flows are never touched automatically.
"""

import json
import datetime
from pathlib import Path
from typing import Optional


# ── Artifact file names ───────────────────────────────────────────────────────
DRAFT_ARTIFACT = "mvp_requirements_draft.md"
QUESTIONS_ARTIFACT = "requirements_questions.json"
CONVERSATION_ARTIFACT = "requirements_conversation.md"
APPROVED_ARTIFACT = "approved_requirements.md"
SIGNOFF_ARTIFACT = "requirements_signoff_state.json"

# Entry points that have full conversation support
SUPPORTED_ENTRY_POINTS = frozenset({
    "raw_idea", "written_requirements", "existing_app_upgrade",
})

# Valid question types
QUESTION_TYPES = frozenset({
    "single_choice", "multi_choice", "short_text", "long_text", "yes_no",
})

# Valid requirements statuses
REQUIREMENTS_STATUSES = frozenset({
    "not_started", "draft", "questions_pending", "review", "approved",
})


# ── Draft generation ──────────────────────────────────────────────────────────

def generate_requirements_draft(
    entry_point: str,
    context: Optional[dict] = None,
) -> str:
    """
    Generate a template-based requirements draft. No LLM call required.

    context keys by entry_point:
      raw_idea:             "raw_input" (str)
      written_requirements: "requirements_text" (str)
      existing_app_upgrade: "existing_app_path" (str), "feature_request" (str),
                             "existing_app_summary" (str, optional)
    """
    ctx = context or {}
    if entry_point == "raw_idea":
        return _draft_raw_idea(ctx.get("raw_input", "(no idea text provided)"))
    if entry_point == "written_requirements":
        return _draft_written_requirements(ctx.get("requirements_text", "(no requirements text provided)"))
    if entry_point == "existing_app_upgrade":
        return _draft_existing_app_upgrade(
            app_path=ctx.get("existing_app_path", "(unknown app)"),
            feature_request=ctx.get("feature_request", "(no feature request provided)"),
            existing_app_summary=ctx.get("existing_app_summary", ""),
        )
    return _draft_raw_idea(ctx.get("raw_input", "(no idea text provided)"))


def _draft_raw_idea(raw_input: str) -> str:
    return f"""# MVP Requirements Draft

> **Status:** Draft — awaiting your answers to the requirements questions below.

## Product Summary

{raw_input}

## Target Users

*To be clarified via requirements questions.*

## Core User Workflows

*To be clarified. The single most important workflow will drive the MVP scope.*

## Must-Have Features

*Determined by answering: "What must work for the MVP to be usable at all?"*

## Nice-to-Have Later

*Features that would be great but are explicitly deferred from v1.*

## Out of Scope for V1

*Explicitly excluded to prevent scope creep.*

## Data and External Services

*Data sources (mock / local / database / external API) to be confirmed.*

## Acceptance Criteria

*Will be generated once questions are answered.*
"""


def _draft_written_requirements(requirements_text: str) -> str:
    return f"""# Normalized Requirements Draft

> **Status:** Draft — reviewing supplied requirements for gaps and completeness.

## Summary

The following requirements were provided:

{requirements_text}

## Functional Requirements

*Extracted from the above. Must-have items will be confirmed via gap questions.*

## Missing Details

*Gap-filling questions will identify what is not yet specified.*

## External Dependencies

*Any external APIs, data sources, or services not yet specified.*

## Acceptance Criteria

*Will be finalized once gap questions are answered.*
"""


def _draft_existing_app_upgrade(
    app_path: str,
    feature_request: str,
    existing_app_summary: str,
) -> str:
    app_name = Path(app_path).name if app_path else "existing app"
    summary_section = (
        f"\n{existing_app_summary}\n" if existing_app_summary
        else "*App scan summary will be available after planning step.*"
    )
    return f"""# Upgrade Requirements Draft

> **Status:** Draft — confirm scope, reuse, and non-goals before architecture planning.

## Existing App Context

**App:** `{app_name}` (`{app_path}`)

{summary_section}

## Feature Request

{feature_request}

## Preserve / Reuse

*Which existing pages, components, patterns, and conventions must be preserved?*
*To be confirmed via requirements questions.*

## Additive Changes

*Is this upgrade purely additive, or does it modify existing behavior?*
*To be confirmed.*

## Must-Have Upgrade Behavior

*The minimum set of new behaviors this upgrade must deliver.*

## Out of Scope

*What will explicitly NOT be changed in this upgrade.*

## Risk Notes

*Backend/database/auth changes carry higher risk. Scope will be confirmed.*

## Acceptance Criteria

*Will be generated once scope questions are answered.*
"""


# ── Question templates ────────────────────────────────────────────────────────

def generate_requirements_questions(
    entry_point: str,
    draft_text: str = "",
    context: Optional[dict] = None,
) -> list[dict]:
    """
    Return a list of structured question dicts for the given entry point.
    Each dict has: id, label, question, type, options, recommended, answer,
    freeform_answer, why, required.
    """
    if entry_point == "raw_idea":
        return _questions_raw_idea(context or {})
    if entry_point == "written_requirements":
        return _questions_written_requirements(context or {})
    if entry_point == "existing_app_upgrade":
        return _questions_existing_app_upgrade(context or {})
    # default fallback
    return _questions_raw_idea(context or {})


def _q(
    id: str,
    label: str,
    question: str,
    type: str,
    options: Optional[list] = None,
    recommended: str = "",
    why: str = "",
    required: bool = True,
) -> dict:
    return {
        "id": id,
        "label": label,
        "question": question,
        "type": type,
        "options": options or [],
        "recommended": recommended,
        "answer": None,
        "freeform_answer": "",
        "why": why,
        "required": required,
    }


def _questions_raw_idea(context: dict) -> list[dict]:
    return [
        _q(
            "primary_user",
            "Primary user",
            "Who is the primary user of this MVP?",
            "short_text",
            why="Knowing the primary user shapes every UX and workflow decision.",
        ),
        _q(
            "core_workflow",
            "Core workflow",
            "What is the single most important user workflow for the MVP?",
            "long_text",
            why="One golden path keeps the MVP focused and buildable in one sprint.",
        ),
        _q(
            "user_accounts",
            "User accounts in v1",
            "Should users need accounts / login in v1?",
            "yes_no",
            recommended="No",
            why="Auth adds significant complexity. Deferring it keeps the first sprint lean.",
        ),
        _q(
            "data_source",
            "Data source",
            "What should the MVP use for data?",
            "single_choice",
            options=["Mock data", "Local JSON file", "Database seed data", "External API"],
            recommended="Mock data",
            why="Mock data keeps the first MVP reliable without requiring paid APIs or a seeded database.",
        ),
        _q(
            "must_have_features",
            "Must-have features",
            "Which features MUST be present for the MVP to be usable?",
            "long_text",
            why="Distinguishes the non-negotiable core from nice-to-haves.",
        ),
        _q(
            "nice_to_have",
            "Nice-to-have (later)",
            "Which features would be great but should be explicitly deferred from v1?",
            "long_text",
            recommended="",
            why="Explicit deferral prevents scope creep during the build.",
            required=False,
        ),
        _q(
            "out_of_scope",
            "Out of scope for v1",
            "What is explicitly out of scope for v1?",
            "long_text",
            why="Stating non-goals prevents the build from expanding unexpectedly.",
        ),
    ]


def _questions_written_requirements(context: dict) -> list[dict]:
    return [
        _q(
            "must_have_v1",
            "Must-have for v1",
            "Which of the listed features are must-have for v1?",
            "long_text",
            why="Prioritizes the non-negotiable features so the sprint stays focused.",
        ),
        _q(
            "defer_later",
            "Defer to later",
            "Which features should be explicitly deferred from v1?",
            "long_text",
            required=False,
            why="Explicit deferral prevents the first sprint from becoming too large.",
        ),
        _q(
            "auth_included",
            "Auth / database / API included?",
            "Do the requirements include auth, database, or external API calls in v1?",
            "yes_no",
            recommended="No",
            why="Auth and API integrations multiply complexity. Knowing this up front shapes the architecture.",
        ),
        _q(
            "non_goals",
            "Non-goals",
            "What are the explicit non-goals for this build?",
            "long_text",
            required=False,
            why="Non-goals are as important as goals for scoping a buildable sprint.",
        ),
        _q(
            "external_dependencies",
            "External dependencies",
            "Are there external data sources, APIs, or services this MVP depends on?",
            "long_text",
            required=False,
            why="External dependencies need mocking or integration planning before build.",
        ),
    ]


def _questions_existing_app_upgrade(context: dict) -> list[dict]:
    return [
        _q(
            "preserve_pages",
            "Pages/components to preserve",
            "Which existing pages, components, or flows must be preserved exactly as-is?",
            "long_text",
            why="Explicit preservation scope prevents accidental regressions.",
        ),
        _q(
            "additive_only",
            "Additive-only change",
            "Should this upgrade be strictly additive — no existing behavior changed or removed?",
            "yes_no",
            recommended="Yes",
            why="Additive-only upgrades are safer, faster, and easier to review.",
        ),
        _q(
            "backend_db_allowed",
            "Backend / database changes allowed",
            "Are backend or database schema changes allowed in this upgrade?",
            "yes_no",
            recommended="No",
            why="Backend and schema changes carry higher risk. Frontend-only upgrades are safer for the first sprint.",
        ),
        _q(
            "frontend_first",
            "Frontend-first sprint",
            "Should the first buildable sprint be frontend-only?",
            "yes_no",
            recommended="Yes",
            why="Frontend-only sprints are faster to build, review, and validate.",
        ),
        _q(
            "reuse_patterns",
            "Patterns to reuse",
            "Which current app patterns, component libraries, or conventions must the upgrade follow?",
            "long_text",
            required=False,
            why="Reusing established patterns keeps the upgrade consistent with the existing codebase.",
        ),
        _q(
            "out_of_scope",
            "Out of scope for this upgrade",
            "What is explicitly out of scope for this upgrade?",
            "long_text",
            why="Non-goals prevent the upgrade from expanding beyond the agreed feature set.",
        ),
        _q(
            "new_vs_existing_files",
            "New modules or extend existing files",
            "Should this feature create new modules or extend existing files?",
            "single_choice",
            options=["Extend existing files", "Create new modules", "Both — as appropriate"],
            recommended="Extend existing files",
            why="Extending existing files is lower-risk; new modules are cleaner for large features.",
            required=False,
        ),
    ]


# ── State management ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_requirements_conversation(run_dir: Path) -> Optional[dict]:
    """Load conversation state from requirements_questions.json, or None if absent."""
    return _safe_read_json(Path(run_dir) / QUESTIONS_ARTIFACT)


def init_requirements_conversation(
    run_dir: Path,
    entry_point: str,
    context: Optional[dict] = None,
) -> dict:
    """
    Initialize and persist a fresh requirements conversation for a run.
    Writes mvp_requirements_draft.md, requirements_questions.json,
    and requirements_conversation.md.

    If the conversation already exists, returns the existing state unchanged.
    """
    run_dir = Path(run_dir)
    existing = load_requirements_conversation(run_dir)
    if existing is not None:
        return existing

    ctx = context or {}
    draft = generate_requirements_draft(entry_point, ctx)
    questions = generate_requirements_questions(entry_point, draft, ctx)

    # Write draft artifact
    (run_dir / DRAFT_ARTIFACT).write_text(draft, encoding="utf-8")

    state = {
        "entry_point": entry_point,
        "requirements_status": "questions_pending",
        "questions": questions,
        "answers": {},
        "draft_requirements_artifact": DRAFT_ARTIFACT,
        "approved_requirements_artifact": None,
        "requirements_approved": False,
        "updated_at": _now_iso(),
    }
    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)
    return state


def lazy_init_from_run_state(run_dir: Path, run_state: dict) -> dict:
    """
    Lazily initialize a requirements conversation from an existing run's state.
    Called when the frontend opens the requirements conversation for a run that
    predates the conversation system.
    """
    run_dir = Path(run_dir)
    existing = load_requirements_conversation(run_dir)
    if existing is not None:
        return existing

    # Infer entry point from run_state
    if run_state.get("bugfix_mode"):
        entry_point = "bugfix"
    elif run_state.get("backend_inventory_mode"):
        entry_point = "backend_inventory"
    elif run_state.get("mode") == "existing_app_upgrade" or run_state.get("upgrade_mode"):
        entry_point = "existing_app_upgrade"
    else:
        entry_point = "raw_idea"

    if entry_point not in SUPPORTED_ENTRY_POINTS:
        # Unsupported entry points — return a minimal "not applicable" state
        return {
            "entry_point": entry_point,
            "requirements_status": "not_applicable",
            "questions": [],
            "answers": {},
            "draft_requirements_artifact": None,
            "approved_requirements_artifact": None,
            "requirements_approved": False,
            "updated_at": _now_iso(),
        }

    # Build context from available artifacts
    ctx = {}
    raw_input_path = run_dir / "raw_input.md"
    feature_request_path = run_dir / "feature_request.md"
    feature_request_input_path = run_dir / "feature_request_input.md"
    existing_app_summary_path = run_dir / "existing_app_summary.md"

    if entry_point == "existing_app_upgrade":
        ctx["existing_app_path"] = run_state.get("existing_app_path") or run_state.get("existing_app") or ""
        for fr_path in (feature_request_path, feature_request_input_path, raw_input_path):
            if fr_path.exists():
                ctx["feature_request"] = fr_path.read_text(encoding="utf-8")
                break
        if existing_app_summary_path.exists():
            ctx["existing_app_summary"] = existing_app_summary_path.read_text(encoding="utf-8")
    elif entry_point == "written_requirements":
        for p in (raw_input_path, feature_request_path):
            if p.exists():
                ctx["requirements_text"] = p.read_text(encoding="utf-8")
                break
    else:  # raw_idea
        if raw_input_path.exists():
            ctx["raw_input"] = raw_input_path.read_text(encoding="utf-8")

    return init_requirements_conversation(run_dir, entry_point, ctx)


def save_answer(
    run_dir: Path,
    question_id: str,
    answer: Optional[str],
    freeform_answer: str = "",
) -> dict:
    """
    Update a single question's answer in requirements_questions.json.
    Returns the updated conversation state.
    Raises ValueError if the question_id is not found.
    """
    run_dir = Path(run_dir)
    state = load_requirements_conversation(run_dir)
    if state is None:
        raise ValueError(f"No requirements conversation found in {run_dir}")

    found = False
    for q in state["questions"]:
        if q["id"] == question_id:
            q["answer"] = answer
            q["freeform_answer"] = freeform_answer or ""
            found = True
            break
    if not found:
        raise ValueError(f"Question '{question_id}' not found in conversation")

    # Sync answers dict
    state["answers"][question_id] = answer
    if freeform_answer:
        state.setdefault("freeform_answers", {})[question_id] = freeform_answer

    state["requirements_status"] = "questions_pending"
    state["updated_at"] = _now_iso()

    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)
    return state


def approve_requirements(run_dir: Path) -> dict:
    """
    Approve requirements for this run.

    Validates that all required questions have answers.
    Writes approved_requirements.md and requirements_signoff_state.json.
    Returns {"approved": bool, "state": conversation_state, "error": str or None}.
    """
    run_dir = Path(run_dir)
    state = load_requirements_conversation(run_dir)
    if state is None:
        return {"approved": False, "state": None, "error": "No requirements conversation found."}

    # Check required questions
    unanswered = [
        q["id"] for q in state["questions"]
        if q.get("required", True) and not q.get("answer") and not q.get("freeform_answer")
    ]
    if unanswered:
        return {
            "approved": False,
            "state": state,
            "error": f"Required questions not yet answered: {', '.join(unanswered)}",
        }

    # Generate approved requirements doc
    approved_md = _build_approved_requirements_md(state)
    (run_dir / APPROVED_ARTIFACT).write_text(approved_md, encoding="utf-8")

    # Write signoff state (detected by planning_gate.py)
    now = _now_iso()
    signoff = {
        "status": "approved",
        "approved_at": now,
        "approved_by": "user",
        "approved_requirements_artifact": APPROVED_ARTIFACT,
        "source_draft_artifact": state.get("draft_requirements_artifact") or DRAFT_ARTIFACT,
    }
    _write_json(run_dir / SIGNOFF_ARTIFACT, signoff)

    # Update conversation state
    state["requirements_status"] = "approved"
    state["requirements_approved"] = True
    state["approved_requirements_artifact"] = APPROVED_ARTIFACT
    state["updated_at"] = now
    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)

    return {"approved": True, "state": state, "error": None}


def _build_approved_requirements_md(state: dict) -> str:
    """Generate the approved_requirements.md from answered questions + draft."""
    entry_point = state.get("entry_point", "unknown")
    ep_label = entry_point.replace("_", " ").title()
    lines = [
        f"# Approved Requirements — {ep_label}",
        "",
        f"> **Status:** Approved  ",
        f"> **Approved at:** {_now_iso()}",
        "",
        "## Requirements Summary",
        "",
    ]

    for q in state.get("questions", []):
        ans = q.get("answer") or q.get("freeform_answer") or ""
        if not ans:
            continue
        lines.append(f"### {q.get('label', q['id'])}")
        lines.append("")
        lines.append(f"**Question:** {q['question']}")
        lines.append("")
        lines.append(f"**Answer:** {ans}")
        if q.get("freeform_answer") and q.get("freeform_answer") != ans:
            lines.append("")
            lines.append(f"**Notes:** {q['freeform_answer']}")
        lines.append("")

    lines += [
        "---",
        "",
        "*This document was generated from the requirements conversation sign-off.*",
        "*Architecture planning may now proceed.*",
    ]
    return "\n".join(lines)


def _write_conversation_md(run_dir: Path, state: dict) -> None:
    """Write a human-readable markdown transcript of the conversation."""
    entry_point = state.get("entry_point", "unknown")
    ep_label = entry_point.replace("_", " ").title()
    status = state.get("requirements_status", "unknown")
    lines = [
        f"# Requirements Conversation — {ep_label}",
        "",
        f"**Status:** {status.replace('_', ' ').title()}",
        f"**Updated:** {state.get('updated_at', '')}",
        "",
        "---",
        "",
        "## Questions and Answers",
        "",
    ]
    for q in state.get("questions", []):
        ans = q.get("answer") or q.get("freeform_answer") or "_Not yet answered_"
        req_marker = " *(required)*" if q.get("required", True) else ""
        lines.append(f"### {q.get('label', q['id'])}{req_marker}")
        lines.append("")
        lines.append(f"**Q:** {q['question']}")
        if q.get("why"):
            lines.append(f"*Why:* {q['why']}")
        lines.append("")
        lines.append(f"**A:** {ans}")
        lines.append("")

    (run_dir / CONVERSATION_ARTIFACT).write_text("\n".join(lines), encoding="utf-8")


def get_unanswered_required(state: dict) -> list[str]:
    """Return list of required question IDs that have no answer."""
    return [
        q["id"] for q in state.get("questions", [])
        if q.get("required", True) and not q.get("answer") and not q.get("freeform_answer")
    ]
