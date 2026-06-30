"""
Planning Gate — universal pre-build sign-off state model.

Provides:
- build_planning_gate_state(): determines current planning stage + approval status
- workflow_requires_planning_approval(): rule table for which flows need approval
- merge_planning_gate_into_build_gate(): enforces planning gate on top of build gate
- build_planning_gate_from_run_state(): infers gate from existing run_state (older runs safe)

Entry points:
  raw_idea, written_requirements, existing_app_upgrade, bugfix,
  backend_inventory, backend_safety, git_delivery, unknown

Planning stages (in progression order):
  intake → requirements_conversation → requirements_review → requirements_approved
  → architecture_conversation → architecture_review → architecture_approved
  → global_instructions_created → ready_for_build
  (or build_not_applicable for read-only workflows)

Blocked before sign-off:
  Claude Code build, commit, push, PR creation from generated build.

Allowed before sign-off:
  requirements draft/questions, architecture draft/questions, repo scan,
  app inventory, overlap check, sprint planning (plan-only), build gate checking.
"""

import json
from pathlib import Path
from typing import Optional

# ── Entry point constants ─────────────────────────────────────────────────────
ENTRY_POINTS = frozenset({
    "raw_idea",
    "written_requirements",
    "existing_app_upgrade",
    "bugfix",
    "backend_inventory",
    "backend_safety",
    "git_delivery",
    "unknown",
})

# Entry points that require planning approval before any Claude Code build
_BUILD_APPROVAL_REQUIRED = frozenset({
    "raw_idea",
    "written_requirements",
    "existing_app_upgrade",
    "bugfix",
})

# Entry points that are always read-only — never trigger a build
_READ_ONLY_ENTRY_POINTS = frozenset({
    "backend_inventory",
    "backend_safety",
    "git_delivery",
})

# ── Status value constants ────────────────────────────────────────────────────
PLANNING_STAGES = frozenset({
    "intake",
    "requirements_conversation",
    "requirements_review",
    "requirements_approved",
    "architecture_conversation",
    "architecture_review",
    "architecture_approved",
    "global_instructions_created",
    "ready_for_build",
    "build_not_applicable",
    "unknown",
})
REQUIREMENTS_STATUSES = frozenset({
    "not_started", "draft", "questions_pending", "review",
    "approved", "not_applicable", "unknown",
})
ARCHITECTURE_STATUSES = frozenset({
    "not_started", "draft", "questions_pending", "review",
    "approved", "not_applicable", "unknown",
})
GLOBAL_INSTRUCTIONS_STATUSES = frozenset({
    "not_created", "created", "not_applicable", "unknown",
})

# ── Artifact file names (placeholder — full conversation UI is a future prompt) ──
REQUIREMENTS_SIGNOFF_FILE = "requirements_signoff_state.json"
ARCHITECTURE_SIGNOFF_FILE = "architecture_signoff_state.json"
GLOBAL_INSTRUCTIONS_FILE = "GLOBAL_INSTRUCTIONS.md"
APPROVED_REQUIREMENTS_FILE = "approved_requirements.md"
APPROVED_ARCHITECTURE_FILE = "approved_architecture.md"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _detect_requirements_status(run_dir: Optional[Path]) -> str:
    if not run_dir:
        return "not_started"
    state = _safe_read_json(Path(run_dir) / REQUIREMENTS_SIGNOFF_FILE)
    if state is None:
        return "not_started"
    val = state.get("status") or state.get("requirements_status") or "unknown"
    return val if val in REQUIREMENTS_STATUSES else "unknown"


def _detect_architecture_status(run_dir: Optional[Path]) -> str:
    if not run_dir:
        return "not_started"
    state = _safe_read_json(Path(run_dir) / ARCHITECTURE_SIGNOFF_FILE)
    if state is None:
        return "not_started"
    val = state.get("status") or state.get("architecture_status") or "unknown"
    return val if val in ARCHITECTURE_STATUSES else "unknown"


def _detect_global_instructions_status(
    run_dir: Optional[Path],
    existing_app_path: Optional[Path] = None,
) -> str:
    """GLOBAL_INSTRUCTIONS.md can live in the run folder or the target app root."""
    for base in filter(None, [run_dir, existing_app_path]):
        if (Path(base) / GLOBAL_INSTRUCTIONS_FILE).exists():
            return "created"
    return "not_created"


def _derive_planning_stage(
    entry_point: str,
    req_status: str,
    arch_status: str,
    gi_status: str,
    build_requested: bool,
) -> str:
    if entry_point in _READ_ONLY_ENTRY_POINTS:
        return "build_not_applicable"

    if gi_status == "created" and req_status == "approved" and arch_status == "approved":
        return "ready_for_build"
    if arch_status == "approved":
        return "architecture_approved"
    if arch_status in ("review", "questions_pending"):
        return "architecture_review"
    if arch_status == "draft":
        return "architecture_conversation"
    if req_status == "approved":
        return "requirements_approved"
    if req_status in ("review", "questions_pending"):
        return "requirements_review"
    if req_status == "draft":
        return "requirements_conversation"
    return "intake"


# ── Public API ────────────────────────────────────────────────────────────────

def workflow_requires_planning_approval(
    entry_point: str,
    execution_mode: str = "",
    build_requested: bool = True,
) -> bool:
    """
    Return True iff this workflow must have requirements/architecture/global
    instructions sign-off before a Claude Code build is allowed.

    Always False for:
      - read-only workflows (backend_inventory, backend_safety, git_delivery)
      - plan-only runs (execution_mode == "plan_only" or build_requested == False)
    """
    if entry_point in _READ_ONLY_ENTRY_POINTS:
        return False
    if execution_mode == "plan_only" or not build_requested:
        return False
    return entry_point in _BUILD_APPROVAL_REQUIRED


def build_planning_gate_state(
    entry_point: str = "unknown",
    execution_mode: str = "",
    build_requested: bool = True,
    run_dir: Optional[Path] = None,
    existing_app_path: Optional[Path] = None,
    requirements_status: Optional[str] = None,
    architecture_status: Optional[str] = None,
    global_instructions_status: Optional[str] = None,
) -> dict:
    """
    Build the canonical planning gate state object.

    Detects sign-off artifacts from run_dir / existing_app_path if present.
    Status fields can be overridden via direct parameters (useful in tests and
    for runs that already have explicit sign-off data written elsewhere).

    Returns a dict with keys:
      entry_point, planning_stage,
      requirements_status, architecture_status, global_instructions_status,
      requirements_approved, architecture_approved, global_instructions_created,
      build_requires_approval, build_allowed_by_planning_gate, planning_gate_reason
    """
    if entry_point not in ENTRY_POINTS:
        entry_point = "unknown"

    is_read_only = entry_point in _READ_ONLY_ENTRY_POINTS
    is_plan_only = (execution_mode == "plan_only") or (not build_requested)

    # ── Read-only workflows ───────────────────────────────────────────────────
    if is_read_only:
        return {
            "entry_point": entry_point,
            "planning_stage": "build_not_applicable",
            "requirements_status": "not_applicable",
            "architecture_status": "not_applicable",
            "global_instructions_status": "not_applicable",
            "requirements_approved": False,
            "architecture_approved": False,
            "global_instructions_created": False,
            "build_requires_approval": False,
            "build_allowed_by_planning_gate": True,
            "planning_gate_reason": (
                "Planning approval is not required for this read-only workflow."
            ),
        }

    # ── Plan-only runs ────────────────────────────────────────────────────────
    if is_plan_only:
        run_dir_p = Path(run_dir) if run_dir else None
        existing_app_p = Path(existing_app_path) if existing_app_path else None
        req_st = requirements_status if requirements_status is not None else _detect_requirements_status(run_dir_p)
        arch_st = architecture_status if architecture_status is not None else _detect_architecture_status(run_dir_p)
        gi_st = global_instructions_status if global_instructions_status is not None else _detect_global_instructions_status(run_dir_p, existing_app_p)
        return {
            "entry_point": entry_point,
            "planning_stage": _derive_planning_stage(entry_point, req_st, arch_st, gi_st, False),
            "requirements_status": req_st,
            "architecture_status": arch_st,
            "global_instructions_status": gi_st,
            "requirements_approved": req_st == "approved",
            "architecture_approved": arch_st == "approved",
            "global_instructions_created": gi_st == "created",
            "build_requires_approval": False,
            "build_allowed_by_planning_gate": False,
            "planning_gate_reason": "Plan-only run; build not requested.",
        }

    # ── Build-capable flows ───────────────────────────────────────────────────
    run_dir_p = Path(run_dir) if run_dir else None
    existing_app_p = Path(existing_app_path) if existing_app_path else None

    req_st = requirements_status if requirements_status is not None else _detect_requirements_status(run_dir_p)
    arch_st = architecture_status if architecture_status is not None else _detect_architecture_status(run_dir_p)
    gi_st = global_instructions_status if global_instructions_status is not None else _detect_global_instructions_status(run_dir_p, existing_app_p)

    req_approved = req_st == "approved"
    arch_approved = arch_st == "approved"
    gi_created = gi_st == "created"

    needs_approval = workflow_requires_planning_approval(entry_point, execution_mode, build_requested)
    planning_stage = _derive_planning_stage(entry_point, req_st, arch_st, gi_st, build_requested)

    if not needs_approval:
        return {
            "entry_point": entry_point,
            "planning_stage": planning_stage,
            "requirements_status": req_st,
            "architecture_status": arch_st,
            "global_instructions_status": gi_st,
            "requirements_approved": req_approved,
            "architecture_approved": arch_approved,
            "global_instructions_created": gi_created,
            "build_requires_approval": False,
            "build_allowed_by_planning_gate": True,
            "planning_gate_reason": "Planning approval is not required for this workflow.",
        }

    # Approval required — check what is missing in order (req → arch → GI)
    missing = []
    if not req_approved:
        missing.append("requirements")
    if not arch_approved:
        missing.append("architecture sign-off")
    if not gi_created:
        missing.append("global instructions")

    if not missing:
        allowed = True
        reason = "All planning approvals satisfied; build allowed."
    elif missing == ["requirements"]:
        # Requirements not yet done; architecture and GI not yet applicable
        allowed = False
        reason = "Requirements approval is required before architecture planning."
    elif missing == ["architecture sign-off"]:
        allowed = False
        reason = "Architecture approval is required before build."
    elif missing == ["global instructions"]:
        allowed = False
        reason = "GLOBAL_INSTRUCTIONS.md is required before build."
    elif "requirements" not in missing:
        # Requirements approved; only arch and/or GI remain
        allowed = False
        reason = (
            "Claude Code build blocked: architecture sign-off and global instructions "
            "are required before build."
        )
    else:
        # Requirements + others missing
        parts = ", ".join(missing)
        allowed = False
        reason = f"Claude Code build blocked: {parts} are required before build."

    return {
        "entry_point": entry_point,
        "planning_stage": planning_stage,
        "requirements_status": req_st,
        "architecture_status": arch_st,
        "global_instructions_status": gi_st,
        "requirements_approved": req_approved,
        "architecture_approved": arch_approved,
        "global_instructions_created": gi_created,
        "build_requires_approval": True,
        "build_allowed_by_planning_gate": allowed,
        "planning_gate_reason": reason,
    }


def merge_planning_gate_into_build_gate(build_gate: dict, planning_gate_state: dict) -> dict:
    """
    Apply planning gate enforcement on top of an existing build_gate result.

    If the planning gate blocks build, overrides claude_build_allowed → False,
    execution_mode → "build_blocked", and reason → planning_gate_reason.
    If the build_gate already blocked the build, planning gate info is attached
    but the existing block reason is preserved (first gate wins).

    Returns a new dict; does NOT modify build_gate in place.
    """
    result = dict(build_gate)

    # Read-only / plan-only flows: no enforcement needed
    if not planning_gate_state.get("build_requires_approval", False):
        result["planning_gate"] = planning_gate_state
        return result

    # Build already blocked by another gate — attach info, keep original reason
    if not result.get("claude_build_allowed", False):
        result["planning_gate"] = planning_gate_state
        return result

    # Apply planning gate enforcement
    if not planning_gate_state.get("build_allowed_by_planning_gate", False):
        result = dict(build_gate)
        result["claude_build_allowed"] = False
        result["build_allowed"] = False
        result["execution_mode"] = "build_blocked"
        result["reason"] = planning_gate_state["planning_gate_reason"]
        result["planning_gate_blocked"] = True

    result["planning_gate"] = planning_gate_state
    return result


def infer_entry_point_from_run_state(run_state: dict) -> str:
    """
    Classify entry_point from a run's existing run_state.json.
    Used by the backend to categorize older runs without explicit entry_point.
    """
    if run_state.get("bugfix_mode"):
        return "bugfix"
    if run_state.get("backend_inventory_mode"):
        return "backend_inventory"
    if (
        run_state.get("backend_boundary_status") is not None
        or run_state.get("backend_smoke_status") is not None
    ):
        return "backend_safety"
    if run_state.get("pr_remote_decision") or run_state.get("pr_plan_status"):
        return "git_delivery"
    if run_state.get("git_sync_status") is not None:
        return "git_delivery"
    if run_state.get("mode") == "existing_app_upgrade" or run_state.get("upgrade_mode"):
        return "existing_app_upgrade"
    return "unknown"


def build_planning_gate_from_run_state(
    run_state: dict,
    run_dir: Optional[Path] = None,
) -> dict:
    """
    Build planning gate state from a run's run_state.json.

    Safe for older runs: if planning gate fields were never written, falls back
    to inferring entry_point from existing run_state fields and computing the
    gate. Always returns a complete dict — never raises.
    """
    if not run_state:
        return {
            "entry_point": "unknown",
            "planning_stage": "unknown",
            "requirements_status": "unknown",
            "architecture_status": "unknown",
            "global_instructions_status": "unknown",
            "requirements_approved": False,
            "architecture_approved": False,
            "global_instructions_created": False,
            "build_requires_approval": False,
            "build_allowed_by_planning_gate": True,
            "planning_gate_reason": "No run state available.",
        }

    # Already has explicit planning gate fields — return them directly
    if "build_allowed_by_planning_gate" in run_state:
        return {
            "entry_point": run_state.get("entry_point", "unknown"),
            "planning_stage": run_state.get("planning_stage", "unknown"),
            "requirements_status": run_state.get("requirements_status", "unknown"),
            "architecture_status": run_state.get("architecture_status", "unknown"),
            "global_instructions_status": run_state.get("global_instructions_status", "unknown"),
            "requirements_approved": bool(run_state.get("requirements_approved", False)),
            "architecture_approved": bool(run_state.get("architecture_approved", False)),
            "global_instructions_created": bool(run_state.get("global_instructions_created", False)),
            "build_requires_approval": bool(run_state.get("build_requires_approval", False)),
            "build_allowed_by_planning_gate": bool(run_state.get("build_allowed_by_planning_gate", True)),
            "planning_gate_reason": run_state.get("planning_gate_reason", ""),
        }

    # Prefer explicit entry_point if present; fall back to inference for older runs
    entry_point = run_state.get("entry_point") or infer_entry_point_from_run_state(run_state)
    execution_mode = run_state.get("execution_mode", "")
    build_requested = execution_mode not in ("plan_only", "build_blocked")

    return build_planning_gate_state(
        entry_point=entry_point,
        execution_mode=execution_mode,
        build_requested=build_requested,
        run_dir=run_dir,
    )
