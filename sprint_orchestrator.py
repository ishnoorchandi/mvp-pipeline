"""
Sprint Orchestrator — persistent state manager for one sprint at a time.

Manages sprint lifecycle: initialized → build_prompt_ready → build_attempted
→ smoke_check_pending → (smoke_failed | review_pending) → (review_failed |
governance_pending) → (governance_failed | ready_for_completion).

The orchestrator never triggers builds, never calls Claude, and never bypasses
the planning gate. It only records results, computes next_action, and generates
handoff prompts so a future Claude session can resume without restarting.

State is persisted in sprint_orchestrator_state.json inside the run directory.
"""

import json
import datetime
from pathlib import Path
from typing import Optional

# ── File constants ─────────────────────────────────────────────────────────────
STATE_FILE = "sprint_orchestrator_state.json"

# Required documents
_REQUIREMENTS_MD = "requirements.md"
_GLOBAL_INSTRUCTIONS_MD = "GLOBAL_INSTRUCTIONS.md"
_APPROVED_ARCH_MD = "approved_architecture.md"
_SPRINT_PLAN = "feature_sprint_plan.json"
_SPRINT_SCOPE_MD = "selected_feature_sprint_scope.md"
_SPRINT_QUALITY = "sprint_quality_gate.json"

# ── Status / phase constants ───────────────────────────────────────────────────
STATUS_NOT_STARTED = "not_started"
STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"
STATUS_READY = "ready_for_completion"
STATUS_COMPLETED = "completed"

PHASE_INITIALIZED = "initialized"
PHASE_BUILD_PROMPT_READY = "build_prompt_ready"
PHASE_BUILD_ATTEMPTED = "build_attempted"
PHASE_SMOKE_PENDING = "smoke_check_pending"
PHASE_SMOKE_FAILED = "smoke_failed"
PHASE_REVIEW_PENDING = "review_pending"
PHASE_REVIEW_FAILED = "review_failed"
PHASE_GOV_PENDING = "governance_pending"
PHASE_GOV_FAILED = "governance_failed"
PHASE_READY = "ready_for_completion"
PHASE_COMPLETED = "completed"
PHASE_BLOCKED = "blocked"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _result_passed(results: list[dict]) -> bool:
    """True iff the list has at least one entry and every entry is passed/waived."""
    if not results:
        return False
    return all(r.get("status") in ("passed", "waived") for r in results)


def _all_checks_done(state: dict) -> bool:
    """True iff smoke, review, and governance are all passed or waived."""
    return (
        _result_passed(state.get("smoke_checks") or [])
        and _result_passed(state.get("review_checks") or [])
        and _result_passed(state.get("governance_checks") or [])
    )


# ── Gate check ────────────────────────────────────────────────────────────────

def can_initialize_orchestration(
    run_dir: Path,
    run_state: Optional[dict] = None,
) -> tuple[bool, Optional[str]]:
    """
    Return (True, None) if sprint orchestration can be initialized.
    Return (False, reason) if any gate blocks it.

    Checks:
    - planning gate allows build
    - requirements.md exists
    - GLOBAL_INSTRUCTIONS.md exists
    - approved_architecture.md exists
    """
    run_dir = Path(run_dir)

    # Deferred import to avoid circular; planning_gate is a sibling module.
    import planning_gate as pg

    if run_state is None:
        state_path = run_dir / "run_state.json"
        run_state = _read_json(state_path) or {}

    gate = pg.build_planning_gate_from_run_state(run_state, run_dir=run_dir)
    if not gate.get("build_allowed_by_planning_gate", False):
        reason = gate.get("planning_gate_reason") or "Planning gate blocks build."
        return False, f"Sprint orchestration blocked: {reason}"

    missing = []
    if not (run_dir / _REQUIREMENTS_MD).exists():
        missing.append("requirements.md")
    if not (run_dir / _GLOBAL_INSTRUCTIONS_MD).exists():
        missing.append("GLOBAL_INSTRUCTIONS.md")
    if not (run_dir / _APPROVED_ARCH_MD).exists():
        missing.append("approved_architecture.md")

    if missing:
        parts = ", ".join(missing)
        return False, (
            f"Sprint orchestration blocked: {parts} "
            f"{'is' if len(missing) == 1 else 'are'} required before sprint orchestration."
        )

    return True, None


# ── Sprint metadata ───────────────────────────────────────────────────────────

def _load_sprint_meta(run_dir: Path, sprint_number: int) -> dict:
    """Load metadata for the selected sprint from feature_sprint_plan.json."""
    plan = _read_json(run_dir / _SPRINT_PLAN) or {}
    sprints = plan.get("sprints") or []
    sprint = next(
        (s for s in sprints if s.get("sprint_number") == sprint_number), None
    )
    if sprint is None:
        return {"sprint_number": sprint_number, "sprint_title": None, "sprint_goal": None}

    quality = sprint.get("quality") or {}
    return {
        "sprint_number": sprint_number,
        "sprint_title": sprint.get("title"),
        "sprint_goal": sprint.get("goal"),
        "sprint_quality": {
            "build_ready": quality.get("build_ready", True),
            "risk_level": quality.get("risk_level", "unknown"),
            "quality_score": quality.get("quality_score"),
        },
        "likely_files_created": sprint.get("likely_files_created") or [],
        "likely_files_modified": sprint.get("likely_files_modified") or [],
    }


def _check_sprint_build_ready(run_dir: Path, sprint_number: int) -> tuple[bool, Optional[str]]:
    """Return (True, None) if the sprint is build-ready or no quality gate exists."""
    plan = _read_json(run_dir / _SPRINT_PLAN) or {}
    sprints = plan.get("sprints") or []
    sprint = next(
        (s for s in sprints if s.get("sprint_number") == sprint_number), None
    )
    if sprint is None:
        return True, None  # no plan → no quality gate → allow

    quality = sprint.get("quality") or {}
    if quality and quality.get("build_ready") is False:
        return False, (
            f"Sprint orchestration blocked: sprint {sprint_number} needs decomposition "
            "before build (quality gate says not build-ready)."
        )
    return True, None


# ── next_action computation ───────────────────────────────────────────────────

def compute_next_action(state: dict) -> dict:
    """
    Derive next_action and blocking_reason from current state.
    Returns {"next_action": str, "blocking_reason": str|None}.
    Pure function — does not mutate state.
    """
    phase = state.get("current_phase", PHASE_INITIALIZED)
    attempts = state.get("attempts") or []
    last_attempt = attempts[-1] if attempts else None

    if phase == PHASE_BLOCKED or state.get("status") == STATUS_BLOCKED:
        return {
            "next_action": state.get("blocking_reason") or "Resolve blocking issue before continuing.",
            "blocking_reason": state.get("blocking_reason"),
        }

    if phase == PHASE_INITIALIZED:
        return {
            "next_action": "Generate sprint build prompt and start the build.",
            "blocking_reason": None,
        }

    if phase == PHASE_BUILD_PROMPT_READY:
        return {
            "next_action": "Start the sprint build.",
            "blocking_reason": None,
        }

    if phase == PHASE_BUILD_ATTEMPTED:
        if last_attempt:
            attempt_status = last_attempt.get("status")
            if attempt_status == "started":
                return {
                    "next_action": "Continue build or record result.",
                    "blocking_reason": None,
                }
            if attempt_status == "interrupted":
                return {
                    "next_action": "Generate handoff and continue from last completed step.",
                    "blocking_reason": "Build was interrupted.",
                }
            if attempt_status == "failed":
                return {
                    "next_action": "Generate fix prompt from build failure.",
                    "blocking_reason": "Build attempt failed.",
                }
            if attempt_status == "completed":
                return {
                    "next_action": "Run smoke checks and record result.",
                    "blocking_reason": None,
                }
        return {
            "next_action": "Record build attempt result.",
            "blocking_reason": None,
        }

    if phase == PHASE_SMOKE_PENDING:
        return {
            "next_action": "Run smoke checks and record result.",
            "blocking_reason": None,
        }

    if phase == PHASE_SMOKE_FAILED:
        return {
            "next_action": "Create fix prompt for smoke-check failures.",
            "blocking_reason": "Smoke checks failed.",
        }

    if phase == PHASE_REVIEW_PENDING:
        return {
            "next_action": "Run review and record result.",
            "blocking_reason": None,
        }

    if phase == PHASE_REVIEW_FAILED:
        return {
            "next_action": "Create fix prompt for review findings.",
            "blocking_reason": "Review failed.",
        }

    if phase == PHASE_GOV_PENDING:
        return {
            "next_action": "Run governance check and record result.",
            "blocking_reason": None,
        }

    if phase == PHASE_GOV_FAILED:
        return {
            "next_action": "Create fix prompt for governance findings.",
            "blocking_reason": "Governance check failed.",
        }

    if phase == PHASE_READY:
        return {
            "next_action": "Sprint is ready for completion. Approve to mark complete.",
            "blocking_reason": None,
        }

    if phase == PHASE_COMPLETED:
        return {
            "next_action": "Sprint is complete.",
            "blocking_reason": None,
        }

    return {
        "next_action": "Review orchestrator state and determine next step.",
        "blocking_reason": None,
    }


def _apply_next_action(state: dict) -> dict:
    """Compute and write next_action/blocking_reason into state dict in place."""
    result = compute_next_action(state)
    state["next_action"] = result["next_action"]
    state["blocking_reason"] = result["blocking_reason"]
    return state


# ── State management ──────────────────────────────────────────────────────────

def load_orchestrator_state(run_dir: Path) -> Optional[dict]:
    """Load sprint_orchestrator_state.json, or None if absent."""
    return _read_json(Path(run_dir) / STATE_FILE)


def save_orchestrator_state(run_dir: Path, state: dict) -> dict:
    """Persist state to sprint_orchestrator_state.json and return the saved state."""
    state["updated_at"] = _now()
    _write_json(Path(run_dir) / STATE_FILE, state)
    return state


# ── Initialization ────────────────────────────────────────────────────────────

def initialize_orchestrator(
    run_dir: Path,
    sprint_number: int,
    user_note: Optional[str] = None,
    run_state: Optional[dict] = None,
) -> dict:
    """
    Initialize (or idempotently return) orchestrator state for a sprint.

    Raises ValueError with a user-visible reason if gates block.
    If the same sprint is already initialized, returns the existing state unchanged.
    If a different sprint is active, raises ValueError with a 409-like message.
    """
    run_dir = Path(run_dir)

    # Planning gate + document check
    ok, reason = can_initialize_orchestration(run_dir, run_state=run_state)
    if not ok:
        raise ValueError(reason)

    # Sprint quality check
    ready, qual_reason = _check_sprint_build_ready(run_dir, sprint_number)
    if not ready:
        raise ValueError(qual_reason)

    # Idempotency / conflict
    existing = load_orchestrator_state(run_dir)
    if existing is not None:
        active = existing.get("active_sprint")
        if active == sprint_number:
            # Same sprint — idempotent; return as-is
            return existing
        if existing.get("status") not in (STATUS_COMPLETED, STATUS_NOT_STARTED):
            raise ValueError(
                f"Sprint {active} is already active (status={existing.get('status')}). "
                f"Complete or reset it before initializing sprint {sprint_number}."
            )

    meta = _load_sprint_meta(run_dir, sprint_number)
    scope_artifact = _SPRINT_SCOPE_MD if (run_dir / _SPRINT_SCOPE_MD).exists() else None

    now = _now()
    state: dict = {
        "status": STATUS_ACTIVE,
        "active_sprint": sprint_number,
        "sprint_title": meta.get("sprint_title"),
        "sprint_goal": meta.get("sprint_goal"),
        "sprint_quality": meta.get("sprint_quality", {}),
        "current_phase": PHASE_INITIALIZED,
        "last_completed_step": None,
        "next_action": "Generate sprint build prompt and start the build.",
        "blocking_reason": None,
        "requirements_artifact": _REQUIREMENTS_MD,
        "global_instructions_artifact": _GLOBAL_INSTRUCTIONS_MD,
        "approved_architecture_artifact": _APPROVED_ARCH_MD,
        "selected_sprint_artifact": scope_artifact,
        "handoff_artifact": None,
        "user_note": user_note,
        "attempts": [],
        "smoke_checks": [],
        "review_checks": [],
        "governance_checks": [],
        "waivers": [],
        "created_at": now,
        "updated_at": now,
    }

    return save_orchestrator_state(run_dir, state)


# ── Recording functions ───────────────────────────────────────────────────────

def record_build_attempt(
    run_dir: Path,
    status: str,
    summary: Optional[str] = None,
    changed_files: Optional[list] = None,
    artifact: Optional[str] = None,
) -> dict:
    """
    Record a build attempt result and advance phase.

    status: started | completed | failed | interrupted
    """
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        raise ValueError("Orchestrator not initialized. Call initialize_orchestrator first.")

    attempt_number = len(state.get("attempts") or []) + 1
    entry = {
        "attempt_number": attempt_number,
        "status": status,
        "summary": summary,
        "changed_files": changed_files or [],
        "artifact": artifact,
        "recorded_at": _now(),
    }
    if "attempts" not in state:
        state["attempts"] = []
    state["attempts"].append(entry)

    # Advance phase based on attempt status
    if status == "completed":
        state["current_phase"] = PHASE_SMOKE_PENDING
        state["last_completed_step"] = "Build attempt completed."
    elif status == "failed":
        state["current_phase"] = PHASE_BUILD_ATTEMPTED
        state["last_completed_step"] = f"Build attempt {attempt_number} failed."
    elif status == "interrupted":
        state["current_phase"] = PHASE_BUILD_ATTEMPTED
        state["last_completed_step"] = f"Build attempt {attempt_number} interrupted."
    elif status == "started":
        state["current_phase"] = PHASE_BUILD_ATTEMPTED
        state["last_completed_step"] = None

    _apply_next_action(state)
    return save_orchestrator_state(run_dir, state)


def record_smoke_result(
    run_dir: Path,
    status: str,
    summary: Optional[str] = None,
    artifact: Optional[str] = None,
    waived: bool = False,
    waiver_reason: Optional[str] = None,
) -> dict:
    """Record smoke check result (status: passed | failed | waived)."""
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        raise ValueError("Orchestrator not initialized.")

    effective_status = "waived" if waived else status
    entry = {
        "status": effective_status,
        "summary": summary,
        "artifact": artifact,
        "waived": waived,
        "waiver_reason": waiver_reason,
        "recorded_at": _now(),
    }
    if "smoke_checks" not in state:
        state["smoke_checks"] = []
    state["smoke_checks"].append(entry)

    if waived:
        if effective_status == "waived" and waiver_reason:
            state.setdefault("waivers", []).append(
                {"type": "smoke", "reason": waiver_reason, "recorded_at": _now()}
            )

    if effective_status in ("passed", "waived"):
        state["current_phase"] = PHASE_REVIEW_PENDING
        state["last_completed_step"] = "Smoke checks passed."
    else:
        state["current_phase"] = PHASE_SMOKE_FAILED
        state["last_completed_step"] = "Smoke checks ran."

    _check_completion_transition(state)
    _apply_next_action(state)
    return save_orchestrator_state(run_dir, state)


def record_review_result(
    run_dir: Path,
    status: str,
    summary: Optional[str] = None,
    artifact: Optional[str] = None,
    waived: bool = False,
    waiver_reason: Optional[str] = None,
) -> dict:
    """Record code review result (status: passed | failed | waived)."""
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        raise ValueError("Orchestrator not initialized.")

    effective_status = "waived" if waived else status
    entry = {
        "status": effective_status,
        "summary": summary,
        "artifact": artifact,
        "waived": waived,
        "waiver_reason": waiver_reason,
        "recorded_at": _now(),
    }
    if "review_checks" not in state:
        state["review_checks"] = []
    state["review_checks"].append(entry)

    if waived and waiver_reason:
        state.setdefault("waivers", []).append(
            {"type": "review", "reason": waiver_reason, "recorded_at": _now()}
        )

    if effective_status in ("passed", "waived"):
        state["current_phase"] = PHASE_GOV_PENDING
        state["last_completed_step"] = "Code review passed."
    else:
        state["current_phase"] = PHASE_REVIEW_FAILED
        state["last_completed_step"] = "Code review ran."

    _check_completion_transition(state)
    _apply_next_action(state)
    return save_orchestrator_state(run_dir, state)


def record_governance_result(
    run_dir: Path,
    status: str,
    summary: Optional[str] = None,
    artifact: Optional[str] = None,
    waived: bool = False,
    waiver_reason: Optional[str] = None,
) -> dict:
    """Record governance check result (status: passed | failed | waived)."""
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        raise ValueError("Orchestrator not initialized.")

    effective_status = "waived" if waived else status
    entry = {
        "status": effective_status,
        "summary": summary,
        "artifact": artifact,
        "waived": waived,
        "waiver_reason": waiver_reason,
        "recorded_at": _now(),
    }
    if "governance_checks" not in state:
        state["governance_checks"] = []
    state["governance_checks"].append(entry)

    if waived and waiver_reason:
        state.setdefault("waivers", []).append(
            {"type": "governance", "reason": waiver_reason, "recorded_at": _now()}
        )

    if effective_status in ("passed", "waived"):
        state["current_phase"] = PHASE_GOV_PENDING  # tentative; _check_completion may advance
        state["last_completed_step"] = "Governance check passed."
    else:
        state["current_phase"] = PHASE_GOV_FAILED
        state["last_completed_step"] = "Governance check ran."

    _check_completion_transition(state)
    _apply_next_action(state)
    return save_orchestrator_state(run_dir, state)


def _check_completion_transition(state: dict) -> None:
    """
    If smoke + review + governance are all passed/waived, advance to ready_for_completion.
    Mutates state in place. Does not call save_orchestrator_state.
    """
    if _all_checks_done(state):
        state["current_phase"] = PHASE_READY
        state["status"] = STATUS_READY
        state["last_completed_step"] = "All checks passed or waived."


# ── Handoff generation ────────────────────────────────────────────────────────

def generate_handoff(run_dir: Path) -> dict:
    """
    Generate sprint_<n>_handoff.md from current orchestrator state.

    Returns {"success": bool, "artifact": str|None, "error": str|None}.
    """
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        return {"success": False, "artifact": None, "error": "Orchestrator not initialized."}

    sprint_num = state.get("active_sprint", 0)
    artifact_name = f"sprint_{sprint_num}_handoff.md"
    content = _build_handoff_md(state, run_dir)
    _write_text(run_dir / artifact_name, content)

    state["handoff_artifact"] = artifact_name
    save_orchestrator_state(run_dir, state)

    return {"success": True, "artifact": artifact_name, "error": None}


def _fmt_check_list(checks: list) -> str:
    if not checks:
        return "  None recorded.\n"
    lines = []
    for i, c in enumerate(checks, 1):
        status = c.get("status", "unknown")
        summary = c.get("summary") or ""
        artifact = c.get("artifact") or ""
        waiver = c.get("waiver_reason") or ""
        line = f"  {i}. {status.upper()}"
        if summary:
            line += f" — {summary}"
        if artifact:
            line += f" (artifact: {artifact})"
        if waiver:
            line += f" [waived: {waiver}]"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _fmt_attempts(attempts: list) -> str:
    if not attempts:
        return "  None recorded.\n"
    lines = []
    for a in attempts:
        num = a.get("attempt_number", "?")
        status = a.get("status", "unknown")
        summary = a.get("summary") or ""
        files = a.get("changed_files") or []
        artifact = a.get("artifact") or ""
        line = f"  Attempt {num}: {status.upper()}"
        if summary:
            line += f" — {summary}"
        if artifact:
            line += f" (artifact: {artifact})"
        if files:
            line += f" [{len(files)} file(s) changed]"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _build_handoff_md(state: dict, run_dir: Path) -> str:
    sprint_num = state.get("active_sprint", 0)
    title = state.get("sprint_title") or f"Sprint {sprint_num}"
    goal = state.get("sprint_goal") or "(no goal recorded)"
    phase = state.get("current_phase", "unknown")
    last_step = state.get("last_completed_step") or "None"
    next_action = state.get("next_action") or "Review state and determine next step."
    blocking = state.get("blocking_reason") or "None"
    user_note = state.get("user_note") or ""

    req_ref = state.get("requirements_artifact", _REQUIREMENTS_MD)
    arch_ref = state.get("approved_architecture_artifact", _APPROVED_ARCH_MD)
    gi_ref = state.get("global_instructions_artifact", _GLOBAL_INSTRUCTIONS_MD)
    scope_ref = state.get("selected_sprint_artifact") or _SPRINT_SCOPE_MD

    attempts_block = _fmt_attempts(state.get("attempts") or [])
    smoke_block = _fmt_check_list(state.get("smoke_checks") or [])
    review_block = _fmt_check_list(state.get("review_checks") or [])
    gov_block = _fmt_check_list(state.get("governance_checks") or [])

    waivers = state.get("waivers") or []
    waivers_block = (
        "\n".join(f"  - {w.get('type','?').title()}: {w.get('reason','')}" for w in waivers) + "\n"
        if waivers else "  None.\n"
    )

    # Check which docs actually exist so continuation prompt is accurate
    docs_exist = {
        gi_ref: (run_dir / gi_ref).exists(),
        req_ref: (run_dir / req_ref).exists(),
        arch_ref: (run_dir / arch_ref).exists(),
        scope_ref: (run_dir / scope_ref).exists(),
        STATE_FILE: (run_dir / STATE_FILE).exists(),
    }

    read_order_lines = []
    for i, (name, exists) in enumerate(docs_exist.items(), 1):
        exists_note = "" if exists else " (may not exist yet)"
        read_order_lines.append(f"{i}. {name}{exists_note}")
    read_order = "\n".join(read_order_lines)

    note_block = f"\n## User Note\n{user_note}\n" if user_note else ""

    continuation_prompt = (
        f"You are Claude Code continuing Sprint {sprint_num} — {title}.\n"
        "Before editing anything:\n"
        f"1. Read {gi_ref} — this is the source of truth for requirements, architecture, and rules.\n"
        f"2. Read {req_ref} — approved requirements.\n"
        f"3. Read {arch_ref} — approved architecture.\n"
        f"4. Read {scope_ref} — sprint scope and file boundaries.\n"
        f"5. Read {STATE_FILE} — orchestrator state.\n\n"
        f"Do not restart the sprint. Continue from: {phase}.\n"
        f"Last completed step: {last_step}\n"
        f"Next action: {next_action}\n"
        f"Blocking reason: {blocking}\n\n"
        "After completing the next action, record the result using the Sprint Orchestrator API\n"
        "and generate an updated handoff if the session may end soon."
    )

    return f"""# Sprint {sprint_num} Handoff

> Generated at: {_now()}

## Resume Instructions

Continue Sprint {sprint_num} from the recorded orchestrator state.
Do not restart from scratch. Pick up from the last completed step.

## Required Reading Order

{read_order}

## Active Sprint

Sprint {sprint_num} — {title}

**Goal:** {goal}{note_block}

## Current Phase

{phase}

## Last Completed Step

{last_step}

## Next Action

{next_action}

## Blocking Reason

{blocking}

## Approved Requirements

Reference: {req_ref}

## Approved Architecture

Reference: {arch_ref}

## Global Build Constitution

Reference: {gi_ref}

## Sprint Scope

Reference: {scope_ref}

## Build Attempts

{attempts_block}
## Smoke Checks

{smoke_block}
## Review Checks

{review_block}
## Governance Checks

{gov_block}
## Waivers

{waivers_block}
## Continuation Prompt

```
{continuation_prompt}
```
"""
