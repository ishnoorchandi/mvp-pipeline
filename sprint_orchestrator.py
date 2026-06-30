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
_ARCH_QUESTIONS = "architecture_questions.json"
_REQ_QUESTIONS = "requirements_questions.json"

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


# ── Fallback sprint scope (raw idea / written requirements runs) ──────────────

def _lower_first(text: str) -> str:
    return (text[:1].lower() + text[1:]) if text else text


def _as_list(value) -> list:
    if isinstance(value, list):
        return [str(v) for v in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _extract_md_bullets(md_text: str, heading: str, max_items: int = 6) -> list[str]:
    """Extract '- ' / '* ' bullet items under a markdown heading, no LLM involved."""
    heading_lower = heading.strip().lower()
    items: list[str] = []
    in_section = False
    for raw_line in (md_text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            if in_section:
                break
            if line.lstrip("#").strip().lower() == heading_lower:
                in_section = True
            continue
        if in_section and (line.startswith("- ") or line.startswith("* ")):
            item = line[2:].strip()
            if item and not item.startswith("*"):
                items.append(item)
    return items[:max_items]


def _derive_fallback_sprint_scope(
    requirements_text: str,
    arch_answers: dict,
) -> tuple[str, str, list[str], list[str]]:
    """Deterministically derive title/goal/in-scope/out-of-scope from approved docs."""
    frontend = arch_answers.get("frontend_stack") or "React + TypeScript"
    data = arch_answers.get("data_storage") or "Mock data"
    scope_choice = arch_answers.get("first_build_scope") or "Frontend-only MVP"
    auth_scope = str(arch_answers.get("auth_scope") or "No auth in v1")
    deployment_now = str(arch_answers.get("deployment_now") or "No")
    external_services = _as_list(arch_answers.get("external_services")) or ["None"]

    if "applicant tracking" in requirements_text.lower():
        title = "Frontend ATS MVP"
        goal = (
            f"Build a {_lower_first(scope_choice)} applicant tracking dashboard "
            f"using {frontend} and {data.lower()}."
        )
    else:
        title = "Frontend MVP"
        goal = f"Build the approved {_lower_first(scope_choice)} using the approved stack and scope."

    in_scope = _extract_md_bullets(requirements_text, "Must-Have Features")
    if not in_scope:
        in_scope = ["Core MVP functionality described in requirements.md"]

    out_scope = []
    if "no auth" in auth_scope.lower():
        out_scope.append("Authentication")
    if not any("email" in s.lower() for s in external_services):
        out_scope.append("Email automation")
    if not any("payment" in s.lower() for s in external_services):
        out_scope.append("Payments")
    if not external_services or all(s.lower() == "none" for s in external_services):
        out_scope.append("External APIs")
    if deployment_now.strip().lower() in ("no", "false", ""):
        out_scope.append("Deployment")
    if not out_scope:
        out_scope = ["Items outside the approved architecture scope"]

    return title, goal, in_scope, out_scope


def _render_sprint_scope_md(
    title: str, goal: str, in_scope: list[str], out_scope: list[str], arch_answers: dict
) -> str:
    frontend = arch_answers.get("frontend_stack") or "React + TypeScript"
    backend = arch_answers.get("backend_stack") or "No backend / frontend-only"
    data = arch_answers.get("data_storage") or "Mock data"
    auth = arch_answers.get("auth_scope") or "No auth in v1"
    workflow = arch_answers.get("build_workflow") or "Sandbox build"
    scope_choice = arch_answers.get("first_build_scope") or "Frontend-only MVP"

    in_scope_md = "\n".join(f"- {item}" for item in in_scope)
    out_scope_md = "\n".join(f"- {item}" for item in out_scope)

    return f"""# Selected Sprint 1 Scope

## Sprint Title
{title}

## Sprint Goal
{goal}

## Source
Generated fallback sprint scope from approved requirements and architecture because no feature_sprint_plan.json was present.

## In Scope
{in_scope_md}

## Out of Scope
{out_scope_md}

## Approved Architecture
- Frontend: {frontend}
- Backend: {backend}
- Data storage: {data}
- Auth: {auth}
- Build workflow: {workflow}
- First sprint scope: {scope_choice}

## Build Rules
- Read GLOBAL_INSTRUCTIONS.md first.
- Stay within this sprint scope.
- Generate a prompt only; do not run build execution from the app.
"""


def _parse_sprint_scope_md(content: str) -> tuple[Optional[str], Optional[str]]:
    """Read sprint_title/sprint_goal back out of an existing scope artifact."""
    lines = content.splitlines()
    title = None
    goal = None

    def _next_nonblank(idx: int) -> Optional[str]:
        for j in range(idx + 1, min(idx + 4, len(lines))):
            candidate = lines[j].strip()
            if candidate:
                return candidate
        return None

    for idx, raw_line in enumerate(lines):
        line = raw_line.strip().lower()
        if line == "## sprint title":
            title = _next_nonblank(idx) or title
        elif line == "## sprint goal":
            goal = _next_nonblank(idx) or goal

    return title, goal


def ensure_selected_sprint_scope(
    run_dir: Path,
    sprint_number: int,
    entry_point: Optional[str] = None,
) -> dict:
    """
    Ensure selected_feature_sprint_scope.md exists for runs with no
    feature_sprint_plan.json (raw idea / written requirements fallback).

    Deterministic — no LLM calls. Idempotent — never overwrites an existing
    scope artifact; if one already exists, its title/goal are read back instead.

    Returns {"sprint_title": str|None, "sprint_goal": str|None,
             "selected_sprint_artifact": str|None}.
    """
    run_dir = Path(run_dir)
    scope_path = run_dir / _SPRINT_SCOPE_MD

    if scope_path.exists():
        title, goal = _parse_sprint_scope_md(_read_text(scope_path))
        return {
            "sprint_title": title,
            "sprint_goal": goal,
            "selected_sprint_artifact": _SPRINT_SCOPE_MD,
        }

    if not (
        (run_dir / _REQUIREMENTS_MD).exists()
        and (run_dir / _APPROVED_ARCH_MD).exists()
        and (run_dir / _GLOBAL_INSTRUCTIONS_MD).exists()
    ):
        return {"sprint_title": None, "sprint_goal": None, "selected_sprint_artifact": None}

    requirements_text = _read_text(run_dir / _REQUIREMENTS_MD)
    arch_answers = (_read_json(run_dir / _ARCH_QUESTIONS) or {}).get("answers") or {}

    title, goal, in_scope, out_scope = _derive_fallback_sprint_scope(requirements_text, arch_answers)
    content = _render_sprint_scope_md(title, goal, in_scope, out_scope, arch_answers)
    _write_text(scope_path, content)

    return {
        "sprint_title": title,
        "sprint_goal": goal,
        "selected_sprint_artifact": _SPRINT_SCOPE_MD,
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
            "next_action": "Generate sprint build prompt, then copy it into Claude Code manually.",
            "blocking_reason": None,
        }

    if phase == PHASE_BUILD_PROMPT_READY:
        return {
            "next_action": "Copy the build prompt into Claude Code manually, then record the build result.",
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
    if run_state is None:
        run_state = _read_json(run_dir / "run_state.json") or {}

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
    if meta.get("sprint_title") is None:
        # No feature_sprint_plan.json entry for this sprint (raw idea / written
        # requirements runs). Fall back to a deterministically generated scope.
        fallback = ensure_selected_sprint_scope(
            run_dir, sprint_number, entry_point=run_state.get("entry_point")
        )
        sprint_title = fallback.get("sprint_title")
        sprint_goal = fallback.get("sprint_goal")
        scope_artifact = fallback.get("selected_sprint_artifact")
    else:
        sprint_title = meta.get("sprint_title")
        sprint_goal = meta.get("sprint_goal")
        scope_artifact = _SPRINT_SCOPE_MD if (run_dir / _SPRINT_SCOPE_MD).exists() else None

    now = _now()
    state: dict = {
        "status": STATUS_ACTIVE,
        "active_sprint": sprint_number,
        "sprint_title": sprint_title,
        "sprint_goal": sprint_goal,
        "sprint_quality": meta.get("sprint_quality", {}),
        "current_phase": PHASE_INITIALIZED,
        "last_completed_step": None,
        "next_action": None,
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
    _apply_next_action(state)

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


# ── Completion gate ────────────────────────────────────────────────────────────

def _last_check_passed(checks: list) -> bool:
    """True iff the last entry in the list is passed, or waived with a non-empty reason."""
    if not checks:
        return False
    last = checks[-1]
    status = last.get("status")
    if status == "passed":
        return True
    if status == "waived" and last.get("waiver_reason"):
        return True
    return False


def can_complete_sprint(state: dict) -> tuple[bool, Optional[str]]:
    """
    Return (True, None) if the sprint can be marked complete.
    Return (False, reason) if smoke/review/governance checks are not satisfied.

    Rules:
    - Each check list must have at least one entry.
    - The latest entry must be "passed" OR "waived" with a non-empty waiver_reason.
    """
    smoke = state.get("smoke_checks") or []
    review = state.get("review_checks") or []
    governance = state.get("governance_checks") or []

    missing = []
    if not _last_check_passed(smoke):
        missing.append("smoke check not passed or waived with reason")
    if not _last_check_passed(review):
        missing.append("review check not passed or waived with reason")
    if not _last_check_passed(governance):
        missing.append("governance check not passed or waived with reason")

    if missing:
        return False, f"Cannot complete sprint: {'; '.join(missing)}."
    return True, None


# ── Sprint build prompt ────────────────────────────────────────────────────────

def generate_sprint_build_prompt(run_dir: Path) -> dict:
    """
    Generate sprint_<n>_build_prompt.md from orchestrator state.

    Advances phase to build_prompt_ready and records build_prompt_artifact.
    Returns {"success": bool, "artifact": str|None, "error": str|None}.
    """
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        return {"success": False, "artifact": None, "error": "Orchestrator not initialized."}

    sprint_num = state.get("active_sprint", 0)
    artifact_name = f"sprint_{sprint_num}_build_prompt.md"
    content = _build_sprint_build_prompt_md(state, run_dir)
    _write_text(run_dir / artifact_name, content)

    state["build_prompt_artifact"] = artifact_name
    state["current_phase"] = PHASE_BUILD_PROMPT_READY
    state["last_completed_step"] = "Sprint build prompt generated."
    _apply_next_action(state)
    save_orchestrator_state(run_dir, state)

    return {"success": True, "artifact": artifact_name, "error": None}


def _build_sprint_build_prompt_md(state: dict, run_dir: Path) -> str:
    sprint_num = state.get("active_sprint", 0)
    title = state.get("sprint_title") or f"Sprint {sprint_num}"
    goal = state.get("sprint_goal") or "(no goal recorded)"
    phase = state.get("current_phase", "unknown")
    last_step = state.get("last_completed_step") or "None"
    next_action = state.get("next_action") or "None"
    blocking = state.get("blocking_reason") or "None"
    user_note = state.get("user_note") or ""

    quality = state.get("sprint_quality") or {}
    build_ready = quality.get("build_ready", True)
    risk = quality.get("risk_level", "unknown")
    score = quality.get("quality_score")

    req_ref = state.get("requirements_artifact", _REQUIREMENTS_MD)
    arch_ref = state.get("approved_architecture_artifact", _APPROVED_ARCH_MD)
    gi_ref = state.get("global_instructions_artifact", _GLOBAL_INSTRUCTIONS_MD)
    scope_ref = state.get("selected_sprint_artifact") or _SPRINT_SCOPE_MD
    handoff_ref = state.get("handoff_artifact") or f"sprint_{sprint_num}_handoff.md"

    # Load sprint scope excerpt if it exists
    scope_text = _read_text(run_dir / scope_ref)
    scope_excerpt = scope_text[:1500].strip() if scope_text else "(not generated yet)"

    # Likely / matched files
    plan_meta = _load_sprint_meta(run_dir, sprint_num)
    likely_created = plan_meta.get("likely_files_created") or []
    likely_modified = plan_meta.get("likely_files_modified") or []

    likely_block = ""
    if likely_created:
        likely_block += "Files likely to be created:\n" + "\n".join(f"  - {f}" for f in likely_created) + "\n"
    if likely_modified:
        likely_block += "Files likely to be modified:\n" + "\n".join(f"  - {f}" for f in likely_modified) + "\n"
    if not likely_block:
        likely_block = "(see selected_feature_sprint_scope.md)"

    note_block = f"\n**User note:** {user_note}\n" if user_note else ""
    quality_line = f"Build-ready: {build_ready}, Risk: {risk}" + (f", Score: {score}" if score else "")

    return f"""# Sprint {sprint_num} Build Prompt

> **Generated at:** {_now()}
> **These prompts do not run Claude Code automatically. Copy this prompt into Claude Code manually.**

You are Claude Code working on Sprint {sprint_num}.

---

## Mandatory Reading Order

Before editing anything, read these files in order:

1. `{gi_ref}`
2. `{req_ref}`
3. `{arch_ref}`
4. `{scope_ref}`
5. `sprint_orchestrator_state.json`

If a sprint handoff exists, also read:
- `{handoff_ref}`

---

## Non-Negotiable Rules

- Do not restart from scratch.
- Do not ignore `{gi_ref}`.
- Do not change the approved architecture unless explicitly instructed by the user.
- Do not broaden the sprint scope beyond what is listed in `{scope_ref}`.
- Do not touch `.env`, secrets, credentials, `node_modules`, `venv`, generated output folders, or unrelated files.
- Do not commit or push unless the user explicitly asks.
- If token/session limits are reached, stop after summarizing current progress and ask the user to generate a new handoff.
- If the sprint is too broad, stop and request decomposition instead of improvising.

---

## Active Sprint

- **Sprint number:** {sprint_num}
- **Sprint title:** {title}
- **Sprint goal:** {goal}
- **Sprint quality:** {quality_line}
{note_block}
## Likely Files

{likely_block}

## Sprint Scope

{scope_excerpt}

---

## Current Orchestrator State

- **Current phase:** {phase}
- **Last completed step:** {last_step}
- **Next action:** {next_action}
- **Blocking reason:** {blocking}

---

## Required Build Task

Implement only the selected sprint scope from `{scope_ref}`.
Do not implement features from future sprints.
Do not rebuild already-completed sprint work.

---

## Expected Output Back to User

After building, report:

1. Files changed (created / modified / deleted)
2. What was implemented
3. What was intentionally not changed
4. How `{gi_ref}` was followed
5. Any blockers encountered
6. Suggested smoke checks to run
7. Whether a handoff should be generated before the session ends
"""


# ── Sprint fix prompt ──────────────────────────────────────────────────────────

_FAILURE_PHASE_MAP = {
    PHASE_SMOKE_FAILED: "smoke",
    PHASE_REVIEW_FAILED: "review",
    PHASE_GOV_FAILED: "governance",
    PHASE_BUILD_ATTEMPTED: "build",
}


def generate_sprint_fix_prompt(
    run_dir: Path,
    failure_type: Optional[str] = None,
) -> dict:
    """
    Generate sprint_<n>_fix_prompt.md targeting the current failure.

    failure_type: smoke | review | governance | build | None (auto-detected).
    Returns {"success": bool, "artifact": str|None, "error": str|None}.
    """
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        return {"success": False, "artifact": None, "error": "Orchestrator not initialized."}

    # Auto-detect failure type from phase if not provided
    if failure_type is None:
        phase = state.get("current_phase", "")
        failure_type = _FAILURE_PHASE_MAP.get(phase, "unknown")

        # Refine build: check last attempt status
        if failure_type == "build":
            attempts = state.get("attempts") or []
            if attempts and attempts[-1].get("status") == "interrupted":
                failure_type = "interrupted"

    sprint_num = state.get("active_sprint", 0)
    artifact_name = f"sprint_{sprint_num}_fix_prompt.md"
    content = _build_sprint_fix_prompt_md(state, run_dir, failure_type)
    _write_text(run_dir / artifact_name, content)

    state["fix_prompt_artifact"] = artifact_name
    state["last_completed_step"] = "Sprint fix prompt generated."
    state["next_action"] = (
        "Copy sprint fix prompt into Claude Code, apply fix, "
        "then record updated check result."
    )
    save_orchestrator_state(run_dir, state)

    return {"success": True, "artifact": artifact_name, "error": None}


def _get_latest_failed_check(state: dict, failure_type: str) -> dict:
    """Return the latest check record for the given failure type."""
    mapping = {
        "smoke": state.get("smoke_checks") or [],
        "review": state.get("review_checks") or [],
        "governance": state.get("governance_checks") or [],
        "build": state.get("attempts") or [],
        "interrupted": state.get("attempts") or [],
    }
    records = mapping.get(failure_type, [])
    return records[-1] if records else {}


def _build_sprint_fix_prompt_md(state: dict, run_dir: Path, failure_type: str) -> str:
    sprint_num = state.get("active_sprint", 0)
    title = state.get("sprint_title") or f"Sprint {sprint_num}"
    phase = state.get("current_phase", "unknown")

    req_ref = state.get("requirements_artifact", _REQUIREMENTS_MD)
    arch_ref = state.get("approved_architecture_artifact", _APPROVED_ARCH_MD)
    gi_ref = state.get("global_instructions_artifact", _GLOBAL_INSTRUCTIONS_MD)
    scope_ref = state.get("selected_sprint_artifact") or _SPRINT_SCOPE_MD
    handoff_ref = state.get("handoff_artifact") or f"sprint_{sprint_num}_handoff.md"
    handoff_note = f"- `{handoff_ref}` (if it exists)" if (run_dir / handoff_ref).exists() else f"- `{handoff_ref}` (generate one if missing)"

    latest = _get_latest_failed_check(state, failure_type)
    check_summary = latest.get("summary") or "(no summary recorded)"
    check_artifact = latest.get("artifact") or "(none)"
    check_status = latest.get("status") or "unknown"

    failure_label = {
        "smoke": "Smoke check failure",
        "review": "Code review failure",
        "governance": "Governance check failure",
        "build": "Build failure",
        "interrupted": "Build interrupted / session cutoff",
        "unknown": "Unknown failure",
    }.get(failure_type, failure_type.title() + " failure")

    rerun_instruction = {
        "smoke": "After fixing, re-run smoke checks and record the result.",
        "review": "After fixing, request another code review and record the result.",
        "governance": "After fixing, re-run governance check and record the result.",
        "build": "After fixing, record a new build attempt result.",
        "interrupted": "Continue the build from where it stopped. Generate a new handoff when done.",
        "unknown": "After fixing, record the appropriate check result.",
    }.get(failure_type, "After fixing, record the appropriate check result.")

    return f"""# Sprint {sprint_num} Fix Prompt

> **Generated at:** {_now()}
> **These prompts do not run Claude Code automatically. Copy this prompt into Claude Code manually.**

You are Claude Code fixing Sprint {sprint_num} — {title}.

---

## Mandatory Reading Order

Before making any change, read these files in order:

1. `{gi_ref}`
2. `{req_ref}`
3. `{arch_ref}`
4. `{scope_ref}`
5. `sprint_orchestrator_state.json`
{handoff_note}

---

## Failure Context

- **Failure type:** {failure_label}
- **Current phase:** {phase}
- **Latest check status:** {check_status}
- **Summary:** {check_summary}
- **Artifact:** {check_artifact}

---

## Fix Rules

- Fix only the failing issue identified above.
- Do not redesign the sprint scope.
- Do not restart the build from scratch.
- Do not broaden scope to include features from other sprints.
- Preserve all working changes from the previous build attempt.
- Do not commit or push unless explicitly asked.
- Do not touch `.env`, secrets, credentials, `node_modules`, `venv`, or unrelated files.
- If the fix requires changing multiple files, explain why each file needs changing.
- If the root cause is unclear, explain what you found and ask before proceeding.

---

## {rerun_instruction}

---

## Expected Output Back to User

After applying the fix, report:

1. Failure understood — root cause identified
2. Files changed (minimal fix only)
3. Fix applied — what was changed and why
4. Checks to rerun — which smoke/review/governance checks apply
5. Whether smoke/review/governance should be recorded again
6. Whether a new handoff should be generated
"""


# ── Sprint continuation prompt ─────────────────────────────────────────────────

def generate_sprint_continuation_prompt(run_dir: Path) -> dict:
    """
    Generate sprint_<n>_continuation_prompt.md optimised for direct copy/paste
    into a new Claude Code session.

    Returns {"success": bool, "artifact": str|None, "error": str|None}.
    """
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        return {"success": False, "artifact": None, "error": "Orchestrator not initialized."}

    sprint_num = state.get("active_sprint", 0)
    artifact_name = f"sprint_{sprint_num}_continuation_prompt.md"
    content = _build_continuation_prompt_md(state, run_dir)
    _write_text(run_dir / artifact_name, content)

    state["continuation_prompt_artifact"] = artifact_name
    state["last_completed_step"] = "Sprint continuation prompt generated."
    save_orchestrator_state(run_dir, state)

    return {"success": True, "artifact": artifact_name, "error": None}


def _build_continuation_prompt_md(state: dict, run_dir: Path) -> str:
    sprint_num = state.get("active_sprint", 0)
    title = state.get("sprint_title") or f"Sprint {sprint_num}"
    phase = state.get("current_phase", "unknown")
    last_step = state.get("last_completed_step") or "None"
    next_action = state.get("next_action") or "Review orchestrator state and continue."
    blocking = state.get("blocking_reason") or "None"

    req_ref = state.get("requirements_artifact", _REQUIREMENTS_MD)
    arch_ref = state.get("approved_architecture_artifact", _APPROVED_ARCH_MD)
    gi_ref = state.get("global_instructions_artifact", _GLOBAL_INSTRUCTIONS_MD)
    scope_ref = state.get("selected_sprint_artifact") or _SPRINT_SCOPE_MD
    handoff_ref = state.get("handoff_artifact") or f"sprint_{sprint_num}_handoff.md"
    handoff_exists = (run_dir / handoff_ref).exists()

    return f"""# Sprint {sprint_num} Continuation Prompt

> **Generated at:** {_now()}
> **These prompts do not run Claude Code automatically. Copy this prompt into Claude Code manually.**

You are Claude Code continuing Sprint {sprint_num} — {title}.

---

## Before editing anything, read in order:

1. `{gi_ref}`
2. `{req_ref}`
3. `{arch_ref}`
4. `{scope_ref}`
5. `sprint_orchestrator_state.json`
{"6. `" + handoff_ref + "` (handoff exists — read this to resume)" if handoff_exists else "6. `" + handoff_ref + "` (generate a handoff if the session ends)"}

---

## Do not restart from scratch.

Pick up from the current orchestrator state and perform only the next action.

---

## Current State

- **Current phase:** {phase}
- **Last completed step:** {last_step}
- **Next action:** {next_action}
- **Blocking reason:** {blocking}

---

After completing the next action, report what changed and whether any checks need to be recorded.
If the session may end soon, ask the user to generate a new handoff before stopping.
"""


# ── Sprint completion approval ─────────────────────────────────────────────────

def approve_sprint_completion(
    run_dir: Path,
    user_approved: bool = False,
    approval_note: Optional[str] = None,
) -> dict:
    """
    Approve sprint completion. Requires explicit user_approved=True and all
    checks passed or validly waived.

    Returns {"success": bool, "state": dict|None, "error": str|None}.
    """
    run_dir = Path(run_dir)
    state = load_orchestrator_state(run_dir)
    if state is None:
        return {"success": False, "state": None, "error": "Orchestrator not initialized."}

    if not user_approved:
        return {
            "success": False,
            "state": state,
            "error": "Sprint completion requires explicit user approval (user_approved=True).",
        }

    ok, reason = can_complete_sprint(state)
    if not ok:
        return {"success": False, "state": state, "error": reason}

    now = _now()
    sprint_num = state.get("active_sprint", 0)

    state["status"] = STATUS_COMPLETED
    state["current_phase"] = PHASE_COMPLETED
    state["last_completed_step"] = "User approved sprint completion."
    state["next_action"] = (
        "Sprint complete. Select the next sprint or generate final delivery artifacts."
    )
    state["blocking_reason"] = None
    state["completion_approval_artifact"] = f"sprint_{sprint_num}_completion_approval.md"

    # Write completion approval artifact
    approval_content = _build_completion_approval_md(state, sprint_num, approval_note, now)
    _write_text(run_dir / state["completion_approval_artifact"], approval_content)

    save_orchestrator_state(run_dir, state)
    return {"success": True, "state": state, "error": None}


def _check_status_label(checks: list) -> str:
    if not checks:
        return "Not recorded"
    last = checks[-1]
    status = last.get("status", "unknown").upper()
    waiver = last.get("waiver_reason") or ""
    return f"{status}" + (f" (waived: {waiver})" if waiver else "")


def _build_completion_approval_md(
    state: dict, sprint_num: int, approval_note: Optional[str], now: str
) -> str:
    title = state.get("sprint_title") or f"Sprint {sprint_num}"
    note = approval_note or "(none)"
    smoke_label = _check_status_label(state.get("smoke_checks") or [])
    review_label = _check_status_label(state.get("review_checks") or [])
    gov_label = _check_status_label(state.get("governance_checks") or [])

    artifacts = []
    for key in ("requirements_artifact", "global_instructions_artifact",
                "approved_architecture_artifact", "selected_sprint_artifact",
                "handoff_artifact", "build_prompt_artifact", "fix_prompt_artifact",
                "continuation_prompt_artifact"):
        val = state.get(key)
        if val:
            artifacts.append(f"- {val}")

    return f"""# Sprint {sprint_num} Completion Approval

> **Status:** Completed
> **Sprint:** Sprint {sprint_num} — {title}
> **Approved by:** User
> **Completed at:** {now}

## Approval Note

{note}

## Final Check Status

| Check | Status |
|---|---|
| Smoke | {smoke_label} |
| Review | {review_label} |
| Governance | {gov_label} |

## Sprint Artifacts

{chr(10).join(artifacts) if artifacts else "(none recorded)"}
"""
