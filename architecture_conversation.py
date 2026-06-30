"""
Architecture Conversation — interactive architecture sign-off before build.

Universal across all build-capable entry points (raw_idea, written_requirements,
existing_app_upgrade). Architecture conversation can only start after requirements
are approved — `can_start_architecture()` enforces this.

Artifact files written/read per run:
  architecture_draft.md           — draft produced from approved requirements + defaults
  architecture_questions.json     — structured Q&A state (primary storage)
  architecture_conversation.md    — human-readable transcript
  approved_architecture.md        — final approved architecture
  architecture_signoff_state.json — approval record (detected by planning_gate.py)

Lazy-init: conversation state is created on first access if artifacts don't yet
exist AND requirements are approved. If requirements are not yet approved, the
GET endpoint returns can_start=false without writing any files.
"""

import json
import datetime
from pathlib import Path
from typing import Optional


# ── Artifact file names ───────────────────────────────────────────────────────
DRAFT_ARTIFACT = "architecture_draft.md"
QUESTIONS_ARTIFACT = "architecture_questions.json"
CONVERSATION_ARTIFACT = "architecture_conversation.md"
APPROVED_ARTIFACT = "approved_architecture.md"
SIGNOFF_ARTIFACT = "architecture_signoff_state.json"

# Reads from requirements conversation
REQUIREMENTS_SIGNOFF_ARTIFACT = "requirements_signoff_state.json"
APPROVED_REQUIREMENTS_ARTIFACT = "approved_requirements.md"

SUPPORTED_ENTRY_POINTS = frozenset({
    "raw_idea", "written_requirements", "existing_app_upgrade",
})

ARCHITECTURE_STATUSES = frozenset({
    "not_started", "draft", "questions_pending", "review", "approved", "not_applicable",
})


# ── Can-start gate ────────────────────────────────────────────────────────────

def can_start_architecture(run_dir: Path) -> tuple[bool, Optional[str]]:
    """
    Return (True, None) if requirements are approved and architecture conversation
    may be initialized. Return (False, reason_string) otherwise.
    """
    signoff_path = Path(run_dir) / REQUIREMENTS_SIGNOFF_ARTIFACT
    if not signoff_path.exists():
        return False, "Approve requirements before architecture planning."
    try:
        state = json.loads(signoff_path.read_text(encoding="utf-8"))
    except Exception:
        return False, "Requirements signoff file is unreadable."
    if state.get("status") != "approved":
        return False, "Approve requirements before architecture planning."
    return True, None


# ── Draft generation ──────────────────────────────────────────────────────────

def generate_architecture_draft(
    entry_point: str,
    approved_requirements_text: str = "",
    context: Optional[dict] = None,
    answers: Optional[dict] = None,
) -> str:
    """
    Generate a template-based architecture draft. No LLM call required.

    `answers` — dict of question_id → answer value; if present, embeds answered
    choices into the relevant draft sections.
    """
    ctx = context or {}
    ans = answers or {}
    reqs_summary = (approved_requirements_text or "").strip()

    # ── Resolved choices (from answers or defaults) ───────────────────────────
    frontend = ans.get("frontend_stack") or "React + TypeScript *(to be confirmed)*"
    backend = ans.get("backend_stack") or _default_backend(entry_point)
    data = ans.get("data_storage") or "Mock data *(to be confirmed)*"
    auth = ans.get("auth_scope") or "No auth in v1 *(to be confirmed)*"
    services_raw = ans.get("external_services") or ""
    services = services_raw if services_raw else "None *(to be confirmed)*"
    ui = ans.get("ui_style") or "Clean SaaS dashboard *(to be confirmed)*"
    workflow = ans.get("build_workflow") or "Sandbox build *(to be confirmed)*"
    scope = ans.get("first_build_scope") or _default_scope(entry_point)
    deploy = ans.get("deployment_now") or "No *(to be confirmed)*"
    notes = ans.get("architecture_notes") or ""

    context_section = (
        f"\n{reqs_summary}\n" if reqs_summary
        else "*Derived from approved requirements. Requirements context not yet available.*"
    )

    extra_notes = f"\n{notes}" if notes else ""

    return f"""# Architecture Draft

> **Status:** Draft — awaiting your answers to the architecture questions below.

## Product Context

{context_section}

## Selected Stack

| Layer | Choice |
|---|---|
| Frontend | {frontend} |
| Backend | {backend} |
| Data storage | {data} |
| Authentication | {auth} |
| External services | {services} |

## Frontend Architecture

**Stack:** {frontend}

Recommended: Vite build, TypeScript strict mode, component-per-feature structure.
State management: local component state for MVP; global state only if needed.

## Backend Architecture

**Stack:** {backend}

{"No backend in v1 — all data served statically or via mock." if "no backend" in backend.lower() or "frontend-only" in backend.lower() else "REST API with JSON responses. Single-file server for MVP."}

## Data Storage

**Layer:** {data}

{"Mock data served from in-memory fixtures or static JSON files. No database required for v1." if "mock" in data.lower() else f"Using: {data}."}

## Authentication

**Scope:** {auth}

{"Authentication deferred from v1. All pages publicly accessible." if "no auth" in auth.lower() else f"Auth approach: {auth}."}

## External Services

**In scope:** {services}

## UI/UX Direction

**Style:** {ui}

Clean, functional layout. Responsive design. Minimal third-party UI libraries for MVP.

## Build Workflow

**Workflow:** {workflow}

First build: {scope}
Deployment: {deploy}

## Testing and Smoke Checks

Smoke checks: `npm run build` (frontend). Backend: basic import/startup test.
No E2E testing framework required for MVP v1.

## Non-Goals

* No multi-tenancy in v1.
* No CI/CD pipeline setup in v1.
* No production deployment in v1 unless explicitly selected above.

## Risks and Constraints

* External API integrations extend timeline — deferred unless explicitly selected.
* Auth adds 1-2 sprints — deferred unless required.

## Sprint Planning Notes

The first buildable sprint should deliver: {scope}
{extra_notes}
"""


def _default_backend(entry_point: str) -> str:
    if entry_point == "existing_app_upgrade":
        return "No backend / frontend-only *(to be confirmed)*"
    return "No backend / frontend-only *(to be confirmed)*"


def _default_scope(entry_point: str) -> str:
    if entry_point == "existing_app_upgrade":
        return "Existing-app additive sprint *(to be confirmed)*"
    return "Frontend-only MVP *(to be confirmed)*"


# ── Question template ─────────────────────────────────────────────────────────

def generate_architecture_questions(
    entry_point: str,
    approved_requirements_text: str = "",
    context: Optional[dict] = None,
) -> list[dict]:
    """
    Return the universal architecture question list. All 10 questions are shared
    across entry points. Entry point only affects recommended values.
    """
    is_upgrade = entry_point == "existing_app_upgrade"
    return [
        _q(
            "frontend_stack",
            "Frontend stack",
            "What frontend stack should this project use?",
            "single_choice",
            options=["React + TypeScript", "Next.js", "Vue", "Simple HTML/CSS/JS"],
            recommended="React + TypeScript",
            why="React + TypeScript is the default stack for this pipeline and works well for interactive MVPs.",
        ),
        _q(
            "backend_stack",
            "Backend stack",
            "What backend stack should this project use?",
            "single_choice",
            options=["No backend / frontend-only", "Python Flask", "Python FastAPI", "Node Express"],
            recommended="No backend / frontend-only" if is_upgrade else "No backend / frontend-only",
            why="Frontend-only MVPs are faster to build and validate. Add a backend only when persistence or server logic is required.",
        ),
        _q(
            "data_storage",
            "Data storage",
            "What data storage approach should the MVP use?",
            "single_choice",
            options=["Mock data", "Local JSON", "SQLite", "PostgreSQL"],
            recommended="Mock data",
            why="Mock data keeps the first MVP reliable without requiring a database setup or seed scripts.",
        ),
        _q(
            "auth_scope",
            "Authentication scope",
            "Should authentication be included in v1?",
            "single_choice",
            options=["No auth in v1", "Mock auth only", "Email/password auth", "OAuth/social login"],
            recommended="No auth in v1",
            why="Auth adds 1-2 sprints of complexity. Deferring it lets the MVP focus on core functionality.",
        ),
        _q(
            "external_services",
            "External services",
            "Which external services does the MVP need to integrate?",
            "multi_choice",
            options=["None", "Map API", "Listing/data API", "Email service", "Payment service", "AI API"],
            recommended="None",
            why="External service integrations should be minimized for the first MVP sprint.",
        ),
        _q(
            "ui_style",
            "UI style",
            "What UI style should the app follow?",
            "single_choice",
            options=["Clean SaaS dashboard", "Marketplace/search app", "Mobile-first app", "Internal admin tool", "Minimal prototype"],
            recommended="Existing-app additive sprint" if is_upgrade else "Clean SaaS dashboard",
            why="The UI style shapes component structure, layout, and the visual direction of the build.",
        ),
        _q(
            "build_workflow",
            "Build workflow",
            "What build workflow should be used for the first Claude Code sprint?",
            "single_choice",
            options=["Plan only", "Sandbox build", "Direct feature branch build"],
            recommended="Sandbox build",
            why="Sandbox builds let Claude Code build in a disposable copy without touching the real repo until reviewed.",
        ),
        _q(
            "first_build_scope",
            "First build scope",
            "What should the scope of the first buildable sprint be?",
            "single_choice",
            options=["Frontend-only MVP", "Full-stack MVP", "Existing-app additive sprint", "Bugfix only"],
            recommended="Existing-app additive sprint" if is_upgrade else "Frontend-only MVP",
            why="Narrowing the first sprint scope reduces risk and makes the first delivered artifact reviewable in a single session.",
        ),
        _q(
            "deployment_now",
            "Plan deployment now",
            "Should deployment to a hosting environment be planned in this sprint?",
            "yes_no",
            recommended="No",
            why="Deployment planning adds scope. The first MVP sprint should focus on a working local build first.",
        ),
        _q(
            "architecture_notes",
            "Extra architecture constraints",
            "Any extra constraints, preferences, or technologies the architecture must respect?",
            "long_text",
            required=False,
            why="Free-text notes that override or extend any of the above choices.",
        ),
    ]


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


def load_architecture_conversation(run_dir: Path) -> Optional[dict]:
    """Load conversation state from architecture_questions.json, or None if absent."""
    return _safe_read_json(Path(run_dir) / QUESTIONS_ARTIFACT)


def _read_approved_requirements_text(run_dir: Path) -> str:
    path = Path(run_dir) / APPROVED_REQUIREMENTS_ARTIFACT
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def init_architecture_conversation(
    run_dir: Path,
    entry_point: str,
    context: Optional[dict] = None,
    allow_without_requirements: bool = False,
) -> Optional[dict]:
    """
    Initialize and persist a fresh architecture conversation.

    Returns None if requirements are not yet approved and allow_without_requirements
    is False (safe default — callers should check can_start_architecture() first).
    Returns the existing state unchanged if already initialized.
    """
    run_dir = Path(run_dir)
    existing = load_architecture_conversation(run_dir)
    if existing is not None:
        return existing

    ok, blocking = can_start_architecture(run_dir)
    if not ok and not allow_without_requirements:
        return None

    ctx = context or {}
    req_text = _read_approved_requirements_text(run_dir)
    questions = generate_architecture_questions(entry_point, req_text, ctx)
    draft = generate_architecture_draft(entry_point, req_text, ctx)

    (run_dir / DRAFT_ARTIFACT).write_text(draft, encoding="utf-8")

    state = {
        "entry_point": entry_point,
        "architecture_status": "questions_pending",
        "requirements_approved": ok,
        "questions": questions,
        "answers": {},
        "draft_architecture_artifact": DRAFT_ARTIFACT,
        "approved_architecture_artifact": None,
        "architecture_approved": False,
        "updated_at": _now_iso(),
    }
    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)
    return state


def lazy_init_from_run_state(run_dir: Path, run_state: dict) -> dict:
    """
    Lazily initialize architecture conversation from an existing run's state.

    Returns a "blocked" response dict (with can_start=False) if requirements
    are not yet approved — no files are written in that case.
    """
    run_dir = Path(run_dir)
    existing = load_architecture_conversation(run_dir)
    if existing is not None:
        return existing

    # Infer entry point from run_state
    if run_state.get("mode") == "existing_app_upgrade" or run_state.get("upgrade_mode"):
        entry_point = "existing_app_upgrade"
    elif run_state.get("entry_point") in SUPPORTED_ENTRY_POINTS:
        entry_point = run_state["entry_point"]
    elif run_state.get("bugfix_mode") or run_state.get("backend_inventory_mode"):
        return {
            "entry_point": run_state.get("entry_point", "bugfix"),
            "architecture_status": "not_applicable",
            "requirements_approved": False,
            "questions": [],
            "answers": {},
            "draft_architecture_artifact": None,
            "approved_architecture_artifact": None,
            "architecture_approved": False,
            "updated_at": _now_iso(),
        }
    else:
        entry_point = "raw_idea"

    ok, blocking = can_start_architecture(run_dir)
    if not ok:
        return {
            "entry_point": entry_point,
            "architecture_status": "not_started",
            "requirements_approved": False,
            "questions": [],
            "answers": {},
            "draft_architecture_artifact": None,
            "approved_architecture_artifact": None,
            "architecture_approved": False,
            "can_start": False,
            "blocking_reason": blocking,
            "updated_at": _now_iso(),
        }

    ctx = {}
    if entry_point == "existing_app_upgrade":
        ctx["existing_app_path"] = run_state.get("existing_app_path") or run_state.get("existing_app") or ""

    result = init_architecture_conversation(run_dir, entry_point, ctx)
    if result is None:
        return {
            "entry_point": entry_point,
            "architecture_status": "not_started",
            "requirements_approved": False,
            "questions": [],
            "answers": {},
            "draft_architecture_artifact": None,
            "approved_architecture_artifact": None,
            "architecture_approved": False,
            "updated_at": _now_iso(),
        }
    return result


def save_answer(
    run_dir: Path,
    question_id: str,
    answer: Optional[str],
    freeform_answer: str = "",
) -> dict:
    """
    Update a single question's answer in architecture_questions.json.
    Also regenerates architecture_draft.md with the new answers embedded.
    Returns updated state. Raises ValueError if question_id not found.
    """
    run_dir = Path(run_dir)
    state = load_architecture_conversation(run_dir)
    if state is None:
        raise ValueError(f"No architecture conversation found in {run_dir}")

    found = False
    for q in state["questions"]:
        if q["id"] == question_id:
            q["answer"] = answer
            q["freeform_answer"] = freeform_answer or ""
            found = True
            break
    if not found:
        raise ValueError(f"Question '{question_id}' not found in architecture conversation")

    state["answers"][question_id] = answer
    if freeform_answer:
        state.setdefault("freeform_answers", {})[question_id] = freeform_answer

    state["architecture_status"] = "questions_pending"
    state["updated_at"] = _now_iso()

    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)

    # Regenerate draft with current answers
    req_text = _read_approved_requirements_text(run_dir)
    draft = generate_architecture_draft(
        state.get("entry_point", "raw_idea"),
        req_text,
        answers=state["answers"],
    )
    (run_dir / DRAFT_ARTIFACT).write_text(draft, encoding="utf-8")

    return state


def approve_architecture(run_dir: Path) -> dict:
    """
    Approve the architecture conversation.

    Validates all required questions have answers, writes approved_architecture.md
    and architecture_signoff_state.json.
    Returns {"approved": bool, "state": state, "error": str or None}.
    """
    run_dir = Path(run_dir)
    state = load_architecture_conversation(run_dir)
    if state is None:
        return {"approved": False, "state": None, "error": "No architecture conversation found."}

    unanswered = get_unanswered_required(state)
    if unanswered:
        return {
            "approved": False,
            "state": state,
            "error": f"Required questions not yet answered: {', '.join(unanswered)}",
        }

    approved_md = _build_approved_architecture_md(state)
    (run_dir / APPROVED_ARTIFACT).write_text(approved_md, encoding="utf-8")

    now = _now_iso()
    signoff = {
        "status": "approved",
        "approved_at": now,
        "approved_by": "user",
        "approved_architecture_artifact": APPROVED_ARTIFACT,
        "source_draft_artifact": DRAFT_ARTIFACT,
    }
    _write_json(run_dir / SIGNOFF_ARTIFACT, signoff)

    state["architecture_status"] = "approved"
    state["architecture_approved"] = True
    state["approved_architecture_artifact"] = APPROVED_ARTIFACT
    state["updated_at"] = now
    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)

    return {"approved": True, "state": state, "error": None}


def _build_approved_architecture_md(state: dict) -> str:
    entry_point = state.get("entry_point", "unknown")
    ep_label = entry_point.replace("_", " ").title()
    lines = [
        f"# Approved Architecture — {ep_label}",
        "",
        f"> **Status:** Approved  ",
        f"> **Approved at:** {_now_iso()}",
        "",
        "## Architecture Decisions",
        "",
    ]
    for q in state.get("questions", []):
        ans = q.get("answer") or q.get("freeform_answer") or ""
        if not ans:
            continue
        lines.append(f"### {q.get('label', q['id'])}")
        lines.append("")
        lines.append(f"**Decision:** {ans}")
        if q.get("freeform_answer") and q.get("freeform_answer") != ans:
            lines.append(f"**Notes:** {q['freeform_answer']}")
        lines.append("")

    lines += [
        "---",
        "",
        "*This document was generated from the architecture conversation sign-off.*",
        "*Global instructions (GLOBAL_INSTRUCTIONS.md) must be created before build can start.*",
    ]
    return "\n".join(lines)


def _write_conversation_md(run_dir: Path, state: dict) -> None:
    entry_point = state.get("entry_point", "unknown")
    ep_label = entry_point.replace("_", " ").title()
    status = state.get("architecture_status", "unknown")
    lines = [
        f"# Architecture Conversation — {ep_label}",
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
