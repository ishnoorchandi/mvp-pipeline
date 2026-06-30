"""
Global Instructions — GLOBAL_INSTRUCTIONS.md and requirements.md generation.

Two official documents produced after sign-off:

  requirements.md          — final approved requirements document
                             Generated from: approved_requirements.md +
                             requirements_conversation.md + Q&A
                             Requires: requirements sign-off approved

  GLOBAL_INSTRUCTIONS.md   — official build constitution for all Claude sprints
                             Generated from: requirements.md +
                             approved_architecture.md + architecture Q&A
                             Requires: architecture sign-off approved + requirements.md

  global_instructions_state.json — state/metadata for UI/operator summary

The planning_gate.py already detects GLOBAL_INSTRUCTIONS.md via
_detect_global_instructions_status(); this module writes it.
"""

import json
import datetime
from pathlib import Path
from typing import Optional

# ── Artifact names ─────────────────────────────────────────────────────────────
REQUIREMENTS_MD = "requirements.md"
GLOBAL_INSTRUCTIONS_MD = "GLOBAL_INSTRUCTIONS.md"
GI_STATE_FILE = "global_instructions_state.json"

# Source artifacts (from requirements_conversation.py)
_REQ_SIGNOFF = "requirements_signoff_state.json"
_APPROVED_REQ = "approved_requirements.md"
_REQ_CONV = "requirements_conversation.md"
_REQ_DRAFT = "mvp_requirements_draft.md"
_REQ_QUESTIONS = "requirements_questions.json"

# Source artifacts (from architecture_conversation.py)
_ARCH_SIGNOFF = "architecture_signoff_state.json"
_APPROVED_ARCH = "approved_architecture.md"
_ARCH_CONV = "architecture_conversation.md"
_ARCH_QUESTIONS = "architecture_questions.json"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _req_signoff_approved(run_dir: Path) -> bool:
    s = _read_json(run_dir / _REQ_SIGNOFF)
    return bool(s and s.get("status") == "approved")


def _arch_signoff_approved(run_dir: Path) -> bool:
    s = _read_json(run_dir / _ARCH_SIGNOFF)
    return bool(s and s.get("status") == "approved")


# ── Gate checks ────────────────────────────────────────────────────────────────

def can_generate_requirements(run_dir: Path) -> tuple[bool, Optional[str]]:
    """
    Return (True, None) if requirements.md can be generated now.
    Return (False, reason) otherwise.
    """
    run_dir = Path(run_dir)
    if not _req_signoff_approved(run_dir):
        return False, "Requirements must be approved before requirements.md can be generated."
    if not (run_dir / _APPROVED_REQ).exists():
        return False, "approved_requirements.md not found. Run requirements approval first."
    return True, None


def can_generate_global_instructions(run_dir: Path) -> tuple[bool, Optional[str]]:
    """
    Return (True, None) if GLOBAL_INSTRUCTIONS.md can be generated now.
    Return (False, reason) otherwise.
    """
    run_dir = Path(run_dir)
    if not _req_signoff_approved(run_dir):
        return False, "Requirements must be approved before GLOBAL_INSTRUCTIONS.md can be generated."
    if not _arch_signoff_approved(run_dir):
        return False, "Architecture must be approved before GLOBAL_INSTRUCTIONS.md can be generated."
    if not (run_dir / _APPROVED_ARCH).exists():
        return False, "approved_architecture.md not found. Run architecture approval first."
    return True, None


# ── requirements.md generation ─────────────────────────────────────────────────

def generate_requirements_md(run_dir: Path) -> dict:
    """
    Generate requirements.md from approved requirements artifacts.

    Returns {"success": bool, "artifact": str|None, "error": str|None}.
    """
    run_dir = Path(run_dir)
    ok, reason = can_generate_requirements(run_dir)
    if not ok:
        return {"success": False, "artifact": None, "error": reason}

    approved_text = _read(run_dir / _APPROVED_REQ)
    conv_text = _read(run_dir / _REQ_CONV)
    draft_text = _read(run_dir / _REQ_DRAFT)
    questions_data = _read_json(run_dir / _REQ_QUESTIONS) or {}

    content = _build_requirements_md(approved_text, conv_text, draft_text, questions_data)
    _write(run_dir / REQUIREMENTS_MD, content)

    return {"success": True, "artifact": REQUIREMENTS_MD, "error": None}


def _build_requirements_md(
    approved_text: str,
    conv_text: str,
    draft_text: str,
    questions_data: dict,
) -> str:
    questions = questions_data.get("questions") or []
    entry_point = questions_data.get("entry_point") or "unknown"
    ep_label = entry_point.replace("_", " ").title()

    answered = [
        q for q in questions
        if q.get("answer") or q.get("freeform_answer")
    ]

    decisions_block = ""
    if answered:
        lines = []
        for q in answered:
            ans = q.get("answer") or ""
            notes = q.get("freeform_answer") or ""
            lines.append(f"- **{q.get('label', q['id'])}:** {q['question']}")
            lines.append(f"  Answer: {ans}")
            if notes and notes != ans:
                lines.append(f"  Notes: {notes}")
        decisions_block = "\n".join(lines)
    else:
        decisions_block = "*No structured Q&A decisions recorded.*"

    # Use approved_text as the main body if present; it already has sections
    product_body = approved_text.strip() if approved_text.strip() else draft_text.strip()

    source_list = []
    for name in (_APPROVED_REQ, _REQ_CONV, _REQ_DRAFT):
        source_list.append(f"- {name}")

    return f"""# Requirements

> **Status:** Approved
> **Entry point:** {ep_label}
> **Generated at:** {_now_iso()}

## Approved Requirements

{product_body}

## Requirements Decisions

{decisions_block}

## Source Artifacts

{chr(10).join(source_list)}
"""


# ── GLOBAL_INSTRUCTIONS.md generation ─────────────────────────────────────────

def generate_global_instructions(
    run_dir: Path,
    existing_app_path: Optional[str] = None,
) -> dict:
    """
    Generate GLOBAL_INSTRUCTIONS.md and global_instructions_state.json.

    Auto-generates requirements.md first if approved requirements exist but
    requirements.md is missing.

    Returns {"success": bool, "artifacts": list, "error": str|None, "planning_gate": dict|None}.
    """
    run_dir = Path(run_dir)
    ok, reason = can_generate_global_instructions(run_dir)
    if not ok:
        return {"success": False, "artifacts": [], "error": reason, "planning_gate": None}

    # Auto-generate requirements.md if missing
    req_md_path = run_dir / REQUIREMENTS_MD
    if not req_md_path.exists():
        req_result = generate_requirements_md(run_dir)
        if not req_result["success"]:
            return {
                "success": False,
                "artifacts": [],
                "error": f"Could not auto-generate requirements.md: {req_result['error']}",
                "planning_gate": None,
            }

    req_text = _read(run_dir / REQUIREMENTS_MD)
    arch_text = _read(run_dir / _APPROVED_ARCH)
    arch_conv_text = _read(run_dir / _ARCH_CONV)
    arch_questions = _read_json(run_dir / _ARCH_QUESTIONS) or {}

    content = _build_global_instructions(req_text, arch_text, arch_conv_text, arch_questions)
    _write(run_dir / GLOBAL_INSTRUCTIONS_MD, content)

    # Optionally copy to existing_app_path if sandbox/workspace scenario
    artifacts_written = [GLOBAL_INSTRUCTIONS_MD, REQUIREMENTS_MD]
    if existing_app_path:
        app_path = Path(existing_app_path)
        if app_path.exists() and app_path.is_dir():
            try:
                _write(app_path / GLOBAL_INSTRUCTIONS_MD, content)
                _write(app_path / REQUIREMENTS_MD, req_text)
                artifacts_written.append(f"{existing_app_path}/{GLOBAL_INSTRUCTIONS_MD}")
            except Exception:
                pass  # best-effort copy; don't fail generation

    now = _now_iso()
    state = {
        "status": "created",
        "created_at": now,
        "requirements_artifact": REQUIREMENTS_MD,
        "architecture_artifact": _APPROVED_ARCH,
        "global_instructions_artifact": GLOBAL_INSTRUCTIONS_MD,
        "source_artifacts": [REQUIREMENTS_MD, _APPROVED_ARCH, _ARCH_CONV],
    }
    _write_json(run_dir / GI_STATE_FILE, state)

    return {
        "success": True,
        "artifacts": artifacts_written,
        "error": None,
        "planning_gate": None,  # caller fetches from planning_gate module
    }


def _build_global_instructions(
    req_text: str,
    arch_text: str,
    arch_conv_text: str,
    arch_questions: dict,
) -> str:
    """Build the GLOBAL_INSTRUCTIONS.md content from approved documents."""
    answers = arch_questions.get("answers") or {}
    questions = {q["id"]: q for q in (arch_questions.get("questions") or [])}

    def ans(qid: str, default: str = "TBD") -> str:
        a = answers.get(qid)
        if a:
            return a
        q = questions.get(qid)
        return q.get("recommended") or default if q else default

    frontend = ans("frontend_stack", "React + TypeScript")
    backend = ans("backend_stack", "No backend / frontend-only")
    data = ans("data_storage", "Mock data")
    auth = ans("auth_scope", "No auth in v1")
    services = ans("external_services", "None")
    ui = ans("ui_style", "Clean SaaS dashboard")
    workflow = ans("build_workflow", "Sandbox build")
    scope = ans("first_build_scope", "Frontend-only MVP")
    deploy = ans("deployment_now", "No")
    notes = answers.get("architecture_notes") or ""

    product_context = (req_text[:3000].strip() if req_text.strip()
                       else "*See requirements.md for product context.*")
    arch_context = (arch_text[:3000].strip() if arch_text.strip()
                    else "*See approved_architecture.md for architecture details.*")

    extra_notes = f"\n{notes}\n" if notes else ""

    return f"""# GLOBAL_INSTRUCTIONS.md

> **Status:** Approved build instructions
> **Generated at:** {_now_iso()}
> **This file is the source of truth for all Claude Code sprints and builds.**
> **Every Claude Code session must read this file before editing anything.**

---

## Product Vision

{product_context}

---

## Approved Architecture

{arch_context}

---

## Selected Tech Stack

| Layer | Decision |
|---|---|
| Frontend | {frontend} |
| Backend | {backend} |
| Data storage | {data} |
| Authentication | {auth} |
| External services | {services} |
| UI style | {ui} |
| Build workflow | {workflow} |
| First sprint scope | {scope} |
| Deployment in v1 | {deploy} |
{extra_notes}
---

## File and Architecture Rules

- Follow the approved architecture and selected tech stack above.
- Do not introduce a different framework without explicit user approval.
- Do not add external services unless explicitly listed in Selected Tech Stack.
- For existing-app upgrades: preserve all existing app patterns, components, and conventions.
- Keep each sprint's changes inside the selected sprint scope.
- Do not create new top-level folders or restructure the repository without approval.

---

## Build Safety Rules

- Use sandbox workspace unless explicitly approved otherwise.
- Do not push directly to main or master.
- Do not force push to any branch.
- Do not modify `.env`, secrets, or credential files.
- Do not run destructive commands (rm -rf, git reset --hard on tracked files, drop tables).
- Do not bypass repo hygiene, sprint quality gate, planning gate, smoke checks, or governance checks.
- Do not delete existing files unless the sprint plan explicitly lists them as expected deletions.

---

## Sprint Execution Rules

- Every sprint must read GLOBAL_INSTRUCTIONS.md before editing any file.
- Each sprint must stay inside its selected sprint scope (see selected sprint scope artifact).
- If the sprint scope is too broad to complete safely, stop and request decomposition.
- If a Claude session / token limit is reached mid-sprint, stop after writing current state and handoff notes. Do not mark a sprint complete.
- Do not mark a sprint complete until required smoke checks, regression checks, and governance checks pass or are explicitly waived by the user.
- After the build, print a summary of every file created and every file modified.

---

## Handoff and Continuation Rules

When a future Claude session picks up from a sprint handoff or continuation:

1. Read **GLOBAL_INSTRUCTIONS.md** (this file) first.
2. Read **requirements.md** for product requirements.
3. Read **approved_architecture.md** for architecture decisions.
4. Read **selected sprint scope artifact** for the active sprint boundaries.
5. Read **sprint orchestrator handoff** if present — pick up from the last recorded step, do not restart.
6. Do not rebuild previously completed sprints or regenerate the app from scratch.

---

## Sprint Orchestrator Expectations

- A sprint orchestrator may track: active sprint, current phase, last completed step, smoke check results, governance review status, and handoff prompt.
- Claude must not restart work from scratch if a sprint handoff document exists.
- Claude must use the handoff state to continue from the last completed step.
- If the orchestrator marks a step blocked or failed, address the blocking issue before continuing.
- The orchestrator's `next_action` field is the authoritative instruction for the current session.

---

## Non-Goals for v1

- No production deployment unless explicitly selected above.
- No CI/CD pipeline setup.
- No multi-tenancy.
- No authentication beyond what is listed in Selected Tech Stack.
- No external services beyond what is listed in Selected Tech Stack.

---

## Source Artifacts

- requirements.md
- approved_architecture.md
- architecture_conversation.md
"""


# ── State loading ──────────────────────────────────────────────────────────────

def load_global_instructions_state(run_dir: Path) -> Optional[dict]:
    """Load global_instructions_state.json, or None if absent."""
    return _read_json(Path(run_dir) / GI_STATE_FILE)


def get_global_instructions_status(run_dir: Path) -> dict:
    """
    Return a summary dict for the GET /api/runs/<run_id>/global-instructions endpoint.
    Safe for all run ages — never raises.
    """
    run_dir = Path(run_dir)
    req_ok, req_reason = can_generate_requirements(run_dir)
    gi_ok, gi_reason = can_generate_global_instructions(run_dir)
    req_md_exists = (run_dir / REQUIREMENTS_MD).exists()
    gi_exists = (run_dir / GLOBAL_INSTRUCTIONS_MD).exists()
    state = load_global_instructions_state(run_dir)

    # blocking_reason for overall status
    if gi_exists:
        blocking = None
    elif not gi_ok:
        blocking = gi_reason
    else:
        blocking = None

    return {
        "requirements_md_exists": req_md_exists,
        "global_instructions_exists": gi_exists,
        "can_generate_requirements": req_ok,
        "can_generate_global_instructions": gi_ok,
        "blocking_reason": blocking,
        "state": state,
    }
