"""
MVP Pipeline — Backend API
===========================
Serves run data from the runs/ folder.
Runs are file-based (no DB yet).

Routes:
  POST /api/runs                                                    → start a new pipeline run (async)
  GET  /api/runs                                                    → list all runs + status
  GET  /api/runs/<run_id>                                           → full run state + artifact list
  GET  /api/runs/<run_id>/artifacts/<filename>                      → get raw artifact content
  GET  /api/runs/<run_id>/requirements-conversation                 → load (or lazily init) requirements conversation
  POST /api/runs/<run_id>/requirements-conversation/answer          → save one question answer
  POST /api/runs/<run_id>/requirements-conversation/approve         → approve requirements
  GET  /api/runs/<run_id>/architecture-conversation                 → load (or lazily init) architecture conversation
  POST /api/runs/<run_id>/architecture-conversation/answer          → save one architecture answer
  POST /api/runs/<run_id>/architecture-conversation/approve         → approve architecture
  GET  /health                                                      → health check
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, request, abort
from flask_cors import CORS

# ── Setup ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

BASE_DIR = Path(__file__).parent.parent.resolve()
RUNS_DIR = BASE_DIR / "runs"
PIPELINE_SCRIPT = BASE_DIR / "pipeline_mvp_builder.py"

sys.path.insert(0, str(BASE_DIR))
import delivery as delivery_mod  # noqa: E402 — needs BASE_DIR on sys.path first
import planning_gate as planning_gate_mod  # noqa: E402
import requirements_conversation as req_conv_mod  # noqa: E402
import architecture_conversation as arch_conv_mod  # noqa: E402
import global_instructions as gi_mod  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_state(run_id: str) -> dict | None:
    p = RUNS_DIR / run_id / "run_state.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def load_feature_sprint_quality(continue_run: str, sprint_number: int) -> dict | None:
    """Backend-side build guard: reads Sprint Quality Gate metadata for one feature
    sprint from the source run's feature_sprint_plan.json (written by
    write_sprint_quality_gate_artifacts). Returns None when unavailable — e.g. an
    older run without quality metadata — so older runs are never blocked
    (backward compatible). Never writes anything; read-only."""
    source = Path(continue_run)
    if not source.is_absolute():
        source = BASE_DIR / continue_run
    plan_path = source / "feature_sprint_plan.json"
    if not plan_path.exists():
        return None
    try:
        plan = json.loads(plan_path.read_text())
    except Exception:
        return None
    for sprint in plan.get("sprints") or []:
        if sprint.get("sprint_number") == sprint_number:
            return sprint.get("quality")
    return None


# ── Operator Summary ──────────────────────────────────────────────────────────
# Normalizes a run's scattered run_state.json / artifact fields into one
# deterministic, decision-focused summary so the UI can answer "what happened,
# what's the status, is it safe to build/commit/deliver, what's blocking it,
# what's the next safe action, which artifacts matter" without the user having
# to read raw artifacts. Read-only — never writes into the run folder, never
# makes an LLM call, and never breaks on a run that predates these fields
# (everything here degrades to "unknown" rather than crashing or guessing).

WORKFLOW_TYPES = (
    "existing_app_plan", "existing_app_build", "bugfix_plan", "backend_inventory",
    "backend_safety", "git_sync", "pr_delivery", "unknown",
)
EXECUTION_MODES = (
    "plan_only", "build", "build_blocked", "sandbox_build", "bugfix_plan", "inventory", "unknown",
)
BUILD_STATUSES = ("not_run", "blocked", "running", "passed", "failed", "interrupted", "unknown")
DELIVERY_STATUSES = ("not_requested", "blocked", "ready", "committed", "pushed", "pr_opened", "unknown")
REPO_HEALTHS = (
    "clean", "dirty_dependency_files", "dirty_source_files", "dirty_secrets_or_env", "blocked", "unknown",
)

# Priority-ordered — the first N of these that actually exist for a run become
# its primary_artifacts. Keeps the "Primary Outputs" section to the handful of
# files that actually drive a decision, never a dump of everything written.
PRIMARY_ARTIFACT_PRIORITY = [
    "feature_sprint_plan.md", "sprint_quality_gate.md", "existing_feature_overlap_check.md",
    "feature_gap_matrix.md", "additive_architecture.md", "selected_feature_sprint_scope.md",
    "repo_hygiene_summary.md", "sandbox_patch_summary.md", "sandbox_changed_files.md",
    "sandbox_patch.diff", "apply_patch_instructions.md", "minimal_fix_plan.md",
    "backend_route_map.md", "backend_boundary_summary.md", "git_sync_report.md",
    "pr_remote_delivery_report.md", "feature_completion_report.md",
]

_INTERRUPTED_LIKE_STATUSES = {"interrupted", "cancelled"}
_BLOCKED_STATUSES = {
    "build_blocked", "blocked_sprint_not_build_ready", "blocked_dirty_target_repo",
    "blocked_git_pull", "blocked_consistency_violation",
}


def _read_json_artifact(run_dir: Path, filename: str) -> dict | None:
    p = run_dir / filename
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_operator_run_summary(run_dir: Path, run_state: dict, artifacts: list[str]) -> dict:
    """Deterministic, read-only normalization of one run's state + artifacts into
    an operator-focused summary. Never makes an LLM call; never writes anything.
    Missing/older fields always degrade to "unknown" rather than raising."""
    run_state = run_state or {}
    artifact_set = set(artifacts or [])
    status = run_state.get("status") or "unknown"
    raw_execution_mode = run_state.get("execution_mode")
    claude_build_ran = "claude_build_output.txt" in artifact_set

    # ── workflow_type ────────────────────────────────────────────────────────
    if run_state.get("bugfix_mode"):
        workflow_type = "bugfix_plan"
    elif run_state.get("backend_inventory_mode"):
        workflow_type = "backend_inventory"
    elif run_state.get("backend_boundary_status") is not None or run_state.get("backend_smoke_status") is not None:
        workflow_type = "backend_safety"
    elif run_state.get("mode") == "existing_app_upgrade":
        workflow_type = "existing_app_build" if claude_build_ran else "existing_app_plan"
    elif run_state.get("pr_remote_decision") or run_state.get("pr_plan_status"):
        workflow_type = "pr_delivery"
    elif run_state.get("git_sync_status") is not None:
        workflow_type = "git_sync"
    else:
        workflow_type = "unknown"

    # ── execution_mode ───────────────────────────────────────────────────────
    if workflow_type == "bugfix_plan":
        execution_mode = "bugfix_plan"
    elif workflow_type == "backend_inventory":
        execution_mode = "inventory"
    elif raw_execution_mode == "build" and run_state.get("build_workspace_mode") == "sandbox":
        execution_mode = "sandbox_build"
    elif raw_execution_mode in ("plan_only", "build", "build_blocked"):
        execution_mode = raw_execution_mode
    else:
        execution_mode = "unknown"

    # ── build_status ─────────────────────────────────────────────────────────
    if status in _INTERRUPTED_LIKE_STATUSES:
        build_status = "interrupted"
    elif status in _BLOCKED_STATUSES or execution_mode == "build_blocked":
        build_status = "blocked"
    elif status == "building":
        build_status = "running"
    elif claude_build_ran:
        build_failed = (
            run_state.get("regression_status") == "FAIL"
            or run_state.get("change_boundary_status") == "FAIL"
            or bool(run_state.get("smoke_mutation_blocked_delivery"))
        )
        build_status = "failed" if build_failed else "passed"
    elif execution_mode in ("plan_only", "bugfix_plan", "inventory"):
        build_status = "not_run"
    elif status == "unknown":
        build_status = "unknown"
    else:
        build_status = "not_run"

    # ── delivery_status ──────────────────────────────────────────────────────
    delivery_state = _read_json_artifact(run_dir / "delivery", "delivery_state.json")
    pr_remote_decision = run_state.get("pr_remote_decision")
    if pr_remote_decision == "PR_CREATED":
        delivery_status = "pr_opened"
    elif pr_remote_decision == "PUSHED_BRANCH":
        delivery_status = "pushed"
    elif run_state.get("local_delivery_blocked_by_boundary"):
        delivery_status = "blocked"
    elif delivery_state and delivery_state.get("decision") == "BLOCKED":
        delivery_status = "blocked"
    elif delivery_state and delivery_state.get("decision") in ("PASS_LOCAL_ONLY", "PASS_SANDBOX_PUSH"):
        delivery_status = "committed"
    elif run_state.get("pr_plan_status") == "blocked":
        delivery_status = "blocked"
    elif run_state.get("pr_plan_status") in ("ready", "warning", "pr_workflow_required"):
        delivery_status = "ready"
    else:
        delivery_status = "not_requested"

    # ── repo_health ───────────────────────────────────────────────────────────
    hygiene_severity = run_state.get("repo_hygiene_severity")
    hygiene_text = (run_state.get("repo_hygiene_summary_text") or "").lower()
    if hygiene_severity == "clean":
        repo_health = "clean"
    elif hygiene_severity in ("warn", "review", "blocked"):
        if "secret" in hygiene_text or "credential" in hygiene_text or ".env" in hygiene_text:
            repo_health = "dirty_secrets_or_env"
        elif "dependency" in hygiene_text:
            repo_health = "dirty_dependency_files"
        elif "source" in hygiene_text:
            repo_health = "dirty_source_files"
        else:
            repo_health = "blocked" if hygiene_severity == "blocked" else "unknown"
    elif run_state.get("original_repo_modified") is True:
        repo_health = "blocked"
    elif run_state.get("original_repo_modified") is False:
        repo_health = "clean"
    else:
        repo_health = "unknown"

    # ── workspace_mode ────────────────────────────────────────────────────────
    raw_workspace_mode = run_state.get("build_workspace_mode")
    if raw_workspace_mode == "sandbox":
        workspace_mode = "sandbox"
    elif raw_workspace_mode == "direct":
        workspace_mode = "direct_branch"
    elif execution_mode == "plan_only" or workflow_type in ("existing_app_plan", "bugfix_plan", "backend_inventory", "backend_safety"):
        workspace_mode = "planning_only"
    else:
        workspace_mode = "unknown"

    # ── target repo ───────────────────────────────────────────────────────────
    target_repo_path = (
        run_state.get("original_repo_path") or run_state.get("existing_app_path")
        or run_state.get("existing_app") or (delivery_state or {}).get("repo_path")
    )
    target_repo_name = Path(target_repo_path).name if target_repo_path else None

    # ── current_status / blocking_issue / next_safe_action ──────────────────
    sprint_quality = _read_json_artifact(run_dir, "sprint_quality_gate.json")
    sprint_quality_summary = (sprint_quality or {}).get("summary") or {}
    build_ready_count = sprint_quality_summary.get("build_ready_count", 0)
    requires_decomposition_count = sprint_quality_summary.get("requires_decomposition_count", 0)
    if not sprint_quality:
        sprint_quality_status = "unknown"
    elif build_ready_count:
        sprint_quality_status = "has_build_ready_sprints"
    elif requires_decomposition_count:
        sprint_quality_status = "needs_decomposition"
    else:
        sprint_quality_status = "unknown"

    blocking_issue = None
    current_status = None
    next_safe_action = None

    if status == "sandbox_original_repo_modified_warning":
        current_status = "Sandbox build completed with a warning"
        blocking_issue = "Original repo changed unexpectedly during sandbox flow."
        next_safe_action = "Investigate immediately before trusting this run."
    elif status == "blocked_sprint_not_build_ready":
        current_status = "Build blocked"
        blocking_issue = "Selected sprint needs decomposition before build."
        next_safe_action = "Decompose selected sprint."
    elif status == "build_blocked" or execution_mode == "build_blocked":
        current_status = "Build blocked"
        blocking_issue = run_state.get("build_gate_reason") or "Build blocked by safety gate."
        next_safe_action = "Use a sandbox workspace or a prepared feature branch."
    elif status in ("blocked_dirty_target_repo", "blocked_git_pull"):
        current_status = "Build blocked"
        blocking_issue = "Target repo has local changes that must be resolved first."
        next_safe_action = "Resolve repo hygiene issues, then re-run."
    elif status in _INTERRUPTED_LIKE_STATUSES:
        current_status = "Run interrupted before completion."
        next_safe_action = "Ignore or rerun."
    elif status == "started":
        current_status = "Run started but may not have completed."
        next_safe_action = "Check pipeline.log, or rerun."
    elif status == "failed":
        current_status = "Run failed."
        next_safe_action = "Check pipeline.log for the error."
    elif workflow_type == "existing_app_build" and claude_build_ran:
        if execution_mode == "sandbox_build":
            current_status = "Sandbox build completed"
            next_safe_action = "Review sandbox patch"
        else:
            current_status = "Build completed"
            next_safe_action = "Review changes, then run delivery"
    elif workflow_type == "existing_app_plan":
        current_status = "Sprint plan ready"
        next_safe_action = "Review build-ready sprints" if build_ready_count else "Review sprint plan"
    elif workflow_type == "bugfix_plan":
        current_status = "Bugfix plan ready"
        next_safe_action = "Review minimal fix plan"
    elif workflow_type == "backend_inventory":
        current_status = "Backend inventory complete"
        next_safe_action = "Review backend route map"
    elif workflow_type == "backend_safety":
        current_status = "Backend safety analysis complete"
        next_safe_action = "Review backend boundary / smoke plan"
    elif status == "done":
        current_status = "Run complete"
        next_safe_action = "Review outputs"
    elif status == "queued":
        current_status = "Run queued"
        next_safe_action = "Wait for the run to start"
    elif status == "unknown":
        current_status = "Unknown"
        next_safe_action = "Review run details"
    else:
        current_status = str(status).replace("_", " ").capitalize()
        next_safe_action = "Review run details"

    if not blocking_issue and delivery_status == "blocked":
        blocking_issue = "Delivery is blocked — see boundary/smoke mutation report."
    if not blocking_issue and repo_health in ("dirty_dependency_files", "dirty_source_files", "dirty_secrets_or_env"):
        blocking_issue = (
            run_state.get("repo_hygiene_recommended_action")
            or "Repo hygiene issues must be resolved before pulling, building, or delivering."
        )

    primary_artifacts = [a for a in PRIMARY_ARTIFACT_PRIORITY if a in artifact_set][:6]

    # Planning gate — infer from run_state; safe for older runs (falls back to unknown)
    planning_gate = planning_gate_mod.build_planning_gate_from_run_state(
        run_state, run_dir=run_dir,
    )

    return {
        "workflow_type": workflow_type,
        "target_repo_name": target_repo_name,
        "target_repo_path": target_repo_path,
        "execution_mode": execution_mode,
        "build_status": build_status,
        "delivery_status": delivery_status,
        "repo_health": repo_health,
        "sprint_quality_status": sprint_quality_status,
        "workspace_mode": workspace_mode,
        "current_status": current_status,
        "next_safe_action": next_safe_action,
        "blocking_issue": blocking_issue,
        "safe_to_show": True,
        "primary_artifacts": primary_artifacts,
        "planning_gate": planning_gate,
    }


def list_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    runs = []
    for d in sorted(RUNS_DIR.iterdir()):
        if not d.is_dir():
            continue
        state = load_state(d.name)
        if state is None:
            # Directory exists but no state yet — show minimal info
            state = {"run_id": d.name, "status": "unknown", "created": None}
        runs.append({
            "run_id":       state.get("run_id", d.name),
            "status":       state.get("status", "unknown"),
            "created":      state.get("created"),
            "current_step": state.get("current_step"),
            "fix_iteration": state.get("fix_iteration", 0),
            "operator_summary": build_operator_run_summary(d, state, state.get("artifacts") or []),
        })
    return runs


def _spawn_pipeline(run_id: str, cmd: list[str]):
    """Run a pipeline_mvp_builder.py invocation to completion, streaming to pipeline.log.

    Shared by every run-launching path (normal/sprint runs, Existing App Upgrade,
    Sprint Continuation) — only the cmd list differs between them.
    """
    log_file = RUNS_DIR / run_id / "pipeline.log"
    print(f"[backend] Spawning: {' '.join(cmd)}", flush=True)
    try:
        with open(log_file, "w") as lf:
            result = subprocess.run(
                cmd, cwd=str(BASE_DIR),
                stdout=lf, stderr=subprocess.STDOUT
            )
        if result.returncode != 0:
            print(f"[backend] Pipeline exited with code {result.returncode} — see {log_file}", flush=True)
            # Mark run as failed if still queued
            state_path = RUNS_DIR / run_id / "run_state.json"
            try:
                state = json.loads(state_path.read_text())
                if state.get("status") == "queued":
                    state["status"] = "failed"
                    state["error"] = f"Pipeline crashed (exit {result.returncode}). Check runs/{run_id}/pipeline.log"
                    state_path.write_text(json.dumps(state, indent=2))
            except Exception:
                pass
    except Exception as exc:
        print(f"[backend] Failed to spawn pipeline: {exc}", flush=True)


def allocate_run_id() -> str:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted([
        d.name for d in RUNS_DIR.iterdir()
        if d.is_dir() and d.name.startswith("run_")
    ])
    last_num = int(existing[-1].split("_")[1]) if existing else 0
    return f"run_{last_num + 1:03d}"


def run_pipeline_async(
    run_id: str,
    jira_key: str = "",
    mode: str = "",
    plan_only: bool = False,
    sprint_plan: bool = False,
    selected_sprint: int = 1,
    sprint_plan_only: bool = False,
    no_deepseek: bool = False,
):
    """Spawn pipeline_mvp_builder.py in a background thread using pre-allocated run_id.

    plan_only / sprint_plan / selected_sprint / sprint_plan_only map 1:1 onto the
    pipeline's own --plan-only / --sprint-plan / --selected-sprint / --sprint-plan-only
    CLI flags, so dashboard-triggered runs can use the same cheap (no Claude Code,
    no DeepSeek) plan-only paths already supported by the CLI. no_deepseek is an
    additive, optional flag (defaults False, matches prior behavior) used by the
    dashboard's "Run Sprint N" action to keep a single-sprint build cheaper.
    """
    def _run():
        input_file = RUNS_DIR / run_id / "raw_input.md"
        if jira_key:
            cmd = [sys.executable, str(PIPELINE_SCRIPT), "--jira", jira_key, "--run-id", run_id]
        else:
            cmd = [sys.executable, str(PIPELINE_SCRIPT), "--input", str(input_file), "--run-id", run_id]
        if mode and mode != "auto":
            cmd += ["--mode", mode]
        if plan_only:
            cmd += ["--plan-only"]
        if sprint_plan:
            cmd += ["--sprint-plan"]
        if sprint_plan or sprint_plan_only:
            cmd += ["--selected-sprint", str(selected_sprint)]
        if sprint_plan_only:
            cmd += ["--sprint-plan-only"]
        if no_deepseek:
            cmd += ["--no-deepseek"]
        _spawn_pipeline(run_id, cmd)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def run_pipeline_upgrade_async(
    run_id: str,
    existing_app: str,
    feature_request_path: str,
    selected_feature_sprint: int,
    feature_plan_only: bool,
    no_deepseek: bool,
    bugfix_mode: bool = False,
    bug_title: str = "",
    allow_company_build: bool = False,
    use_sandbox_workspace: bool = False,
    sandbox_workspace: str = "",
):
    """Existing App Upgrade mode — maps to --existing-app/--feature-request/--upgrade-mode."""
    def _run():
        cmd = [
            sys.executable, str(PIPELINE_SCRIPT),
            "--existing-app", existing_app,
            "--feature-request", feature_request_path,
            "--upgrade-mode",
            "--selected-feature-sprint", str(selected_feature_sprint),
            "--run-id", run_id,
        ]
        if not bugfix_mode:
            cmd += ["--feature-sprint-plan"]
        if feature_plan_only:
            cmd += ["--feature-plan-only"]
        if allow_company_build:
            cmd += ["--allow-company-build"]
        if sandbox_workspace:
            cmd += ["--sandbox-workspace", sandbox_workspace]
        elif use_sandbox_workspace:
            cmd += ["--use-sandbox-workspace"]
        if no_deepseek:
            cmd += ["--no-deepseek"]
        if bugfix_mode:
            cmd += ["--bugfix-mode", "--bug-report", feature_request_path]
            if bug_title:
                cmd += ["--bug-title", bug_title]
        _spawn_pipeline(run_id, cmd)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def run_pipeline_continuation_async(
    run_id: str,
    continue_run: str,
    continue_sprint: int,
    continue_plan_only: bool,
    no_deepseek: bool,
    feature_sprint: bool = False,
):
    """Sprint Continuation mode — maps to --continue-run/--continue-sprint."""
    def _run():
        cmd = [
            sys.executable, str(PIPELINE_SCRIPT),
            "--continue-run", continue_run,
            "--continue-feature-sprint" if feature_sprint else "--continue-sprint", str(continue_sprint),
            "--run-id", run_id,
        ]
        if continue_plan_only:
            cmd += ["--continue-plan-only"]
        if no_deepseek:
            cmd += ["--no-deepseek"]
        _spawn_pipeline(run_id, cmd)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "pipeline": str(PIPELINE_SCRIPT.exists())})


@app.route("/")
def home():
    return jsonify({"message": "MVP Pipeline backend is running", "version": "1.0"})


@app.route("/api/runs", methods=["GET"])
def get_runs():
    return jsonify(list_runs())


@app.route("/api/runs", methods=["POST"])
def create_run():
    body = request.get_json(force=True, silent=True) or {}

    # Existing App Upgrade and Sprint Continuation have their own payload shapes
    # and CLI mapping — handled by dedicated helpers before falling through to the
    # original idea/requirements/file/jira run-creation path below.
    if body.get("continue_run"):
        return create_continuation_run(body)
    if body.get("upgrade_mode"):
        return create_upgrade_run(body)

    raw_input = body.get("raw_input", "").strip()
    jira_key  = body.get("jira_key",  "").strip().upper()
    mode      = body.get("mode",      "").strip().lower()

    # Plan-only / sprint-plan-only controls — let the dashboard trigger the same
    # cheap, no-Claude-Code / no-DeepSeek paths the CLI already supports.
    plan_only        = bool(body.get("plan_only", False))
    sprint_plan       = bool(body.get("sprint_plan", False))
    sprint_plan_only  = bool(body.get("sprint_plan_only", False))
    try:
        selected_sprint = int(body.get("selected_sprint", 1))
    except (TypeError, ValueError):
        selected_sprint = 1
    if sprint_plan_only:
        sprint_plan = True  # --sprint-plan-only implies --sprint-plan in the pipeline
    no_deepseek = bool(body.get("no_deepseek", False))

    if not raw_input and not jira_key:
        abort(400, "raw_input or jira_key is required")

    run_id = allocate_run_id()
    run_path = RUNS_DIR / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    # Save input file immediately so the pipeline can read it via --input
    display_input = raw_input if raw_input else f"[Jira ticket: {jira_key}]"
    (run_path / "raw_input.md").write_text(display_input)

    state = {
        "run_id": run_id,
        "status": "queued",
        "current_step": "queued",
        "fix_iteration": 0,
        "artifacts": ["raw_input.md"],
        "plan_only": plan_only,
        "sprint_plan": sprint_plan,
        "selected_sprint": selected_sprint,
        "sprint_plan_only": sprint_plan_only,
        "no_deepseek": no_deepseek,
    }
    (run_path / "run_state.json").write_text(json.dumps(state, indent=2))

    run_pipeline_async(
        run_id, jira_key=jira_key, mode=mode,
        plan_only=plan_only, sprint_plan=sprint_plan,
        selected_sprint=selected_sprint, sprint_plan_only=sprint_plan_only,
        no_deepseek=no_deepseek,
    )

    return jsonify({"run_id": run_id, "status": "queued"}), 201


def check_company_repo_build_guard(
    existing_app: str, use_sandbox_workspace: bool, sandbox_workspace: str, allow_company_build: bool,
) -> str | None:
    """Backend-side synchronous guard mirroring resolve_build_gate's company-repo
    policy — read-only, never mutates the target repo. Returns an error message
    if this build request would silently build directly in a company-protected
    repo on a protected branch (or off-branch without an explicit override/
    sandbox), else None. The CLI's own resolve_build_gate is still the
    authoritative, final check — this just avoids queuing a build that the CLI
    would only block after a background subprocess already started."""
    repo_path = Path(existing_app)
    if not existing_app or not (repo_path / ".git").exists():
        return None
    remote_info = delivery_mod.get_git_remote_info(repo_path)
    repo_type = delivery_mod.detect_repo_type(repo_path, remote_info)
    if repo_type != "company-protected":
        return None
    status = delivery_mod.get_git_status(repo_path)
    current_branch = status.get("branch")
    protected_branch = current_branch in delivery_mod.PROTECTED_BRANCHES
    use_sandbox = bool(use_sandbox_workspace or sandbox_workspace)
    if protected_branch and not use_sandbox:
        return ("Claude Code build blocked: company-protected repo requires a sandbox "
                "workspace or prepared feature branch.")
    if not protected_branch and not use_sandbox and not allow_company_build:
        return ("company-protected repo build requires --allow-company-build, a sandbox "
                "workspace, or a prepared feature branch")
    return None


def create_upgrade_run(body: dict):
    """Existing App Upgrade payload → pre-allocated run + --existing-app/--upgrade-mode CLI.

    Defaults feature_plan_only/no_deepseek to True (safety default: no Claude Code
    build or DeepSeek spend unless the dashboard form explicitly unchecks plan-only).
    """
    existing_app = (body.get("existing_app") or "").strip()
    feature_request_text = (body.get("feature_request_text") or body.get("feature_request") or "").strip()
    bugfix_mode = bool(body.get("bugfix_mode", False))
    bug_title = (body.get("bug_title") or "").strip()
    bug_report_text = (body.get("bug_report_text") or body.get("bug_report") or "").strip()
    if bugfix_mode and bug_report_text:
        feature_request_text = bug_report_text
    if not existing_app or not feature_request_text:
        abort(400, "existing_app and feature_request_text are required")
    try:
        selected_feature_sprint = int(body.get("selected_feature_sprint", 1))
    except (TypeError, ValueError):
        selected_feature_sprint = 1
    feature_plan_only = bool(body.get("feature_plan_only", True))
    no_deepseek = bool(body.get("no_deepseek", True))
    allow_company_build = bool(body.get("allow_company_build", False))
    use_sandbox_workspace = bool(body.get("use_sandbox_workspace", False))
    sandbox_workspace = (body.get("sandbox_workspace") or "").strip()
    if sandbox_workspace:
        use_sandbox_workspace = True

    # Backend-side build guard — block before even queuing a run, not just in
    # the frontend. Never silently build directly in a company-protected repo.
    if not feature_plan_only and not bugfix_mode:
        guard_error = check_company_repo_build_guard(
            existing_app, use_sandbox_workspace, sandbox_workspace, allow_company_build,
        )
        if guard_error:
            abort(400, guard_error)

    run_id = allocate_run_id()
    run_path = RUNS_DIR / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    feature_request_path = run_path / "feature_request_input.md"
    feature_request_path.write_text(feature_request_text)

    state = {
        "run_id": run_id,
        "status": "queued",
        "current_step": "queued",
        "fix_iteration": 0,
        "artifacts": [],
        "upgrade_mode": True,
        "bugfix_mode": bugfix_mode,
        "bug_title": bug_title or None,
        "existing_app": existing_app,
        "selected_feature_sprint": selected_feature_sprint,
        "feature_plan_only": feature_plan_only,
        "no_deepseek": no_deepseek,
        "execution_mode": "plan_only" if feature_plan_only else "build",
        "plan_only": feature_plan_only,
        "build_allowed": not feature_plan_only,
        "claude_build_allowed": not feature_plan_only,
        "use_sandbox_workspace": use_sandbox_workspace,
        "sandbox_workspace": sandbox_workspace or None,
    }
    (run_path / "run_state.json").write_text(json.dumps(state, indent=2))

    run_pipeline_upgrade_async(
        run_id, existing_app, str(feature_request_path),
        selected_feature_sprint, feature_plan_only, no_deepseek,
        bugfix_mode=bugfix_mode, bug_title=bug_title, allow_company_build=allow_company_build,
        use_sandbox_workspace=use_sandbox_workspace, sandbox_workspace=sandbox_workspace,
    )

    return jsonify({"run_id": run_id, "status": "queued"}), 201


def create_continuation_run(body: dict):
    """Sprint Continuation payload → pre-allocated run + --continue-run/--continue-sprint CLI.

    Defaults continue_plan_only/no_deepseek to True — actual continuation builds
    aren't fully tested yet, so plan-only is the safe default until a user opts out.
    """
    continue_run = (body.get("continue_run") or "").strip()
    if not continue_run:
        abort(400, "continue_run is required")
    feature_sprint = body.get("continue_feature_sprint") is not None
    try:
        continue_sprint = int(body.get("continue_feature_sprint") if feature_sprint else body.get("continue_sprint", 2))
    except (TypeError, ValueError):
        continue_sprint = 2
    continue_plan_only = bool(body.get("continue_plan_only", True))
    no_deepseek = bool(body.get("no_deepseek", True))

    # Backend-side build guard — block before even queuing a run, not just in the
    # frontend. Frontend-only blocking is not enough when the backend already has
    # this data (sprint["quality"] in the source run's feature_sprint_plan.json).
    if feature_sprint and not continue_plan_only:
        quality = load_feature_sprint_quality(continue_run, continue_sprint)
        if quality and quality.get("build_ready") is False:
            abort(400, quality.get("disabled_reason") or "Sprint is not build-ready; decomposition or review required.")

    run_id = allocate_run_id()
    run_path = RUNS_DIR / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    state = {
        "run_id": run_id,
        "status": "queued",
        "current_step": "queued",
        "fix_iteration": 0,
        "artifacts": [],
        "continue_run": continue_run,
        "continue_sprint": continue_sprint,
        "continue_feature_sprint": continue_sprint if feature_sprint else None,
        "continue_plan_only": continue_plan_only,
        "no_deepseek": no_deepseek,
    }
    (run_path / "run_state.json").write_text(json.dumps(state, indent=2))

    run_pipeline_continuation_async(
        run_id, continue_run, continue_sprint, continue_plan_only, no_deepseek, feature_sprint,
    )

    return jsonify({"run_id": run_id, "status": "queued"}), 201


@app.route("/api/runs/<run_id>/log", methods=["GET"])
def get_pipeline_log(run_id: str):
    log_path = RUNS_DIR / run_id / "pipeline.log"
    if not log_path.exists():
        return jsonify({"log": "(no log yet)"})
    return jsonify({"log": log_path.read_text(encoding="utf-8", errors="replace")[-8000:]})


@app.route("/api/runs/<run_id>", methods=["GET"])
def get_run(run_id: str):
    state = load_state(run_id)
    if state is None:
        abort(404, f"Run {run_id} not found")
    response = dict(state)
    response["operator_summary"] = build_operator_run_summary(
        RUNS_DIR / run_id, state, state.get("artifacts") or [],
    )
    return jsonify(response)


@app.route("/api/runs/<run_id>/artifacts/<filename>", methods=["GET"])
def get_artifact(run_id: str, filename: str):
    # Safety: prevent path traversal
    if ".." in filename or "/" in filename:
        abort(400, "Invalid filename")
    p = RUNS_DIR / run_id / filename
    if not p.exists():
        abort(404, f"{filename} not found in {run_id}")
    content = p.read_text(encoding="utf-8", errors="replace")
    return jsonify({"run_id": run_id, "filename": filename, "content": content})


# ── Requirements Conversation ─────────────────────────────────────────────────

@app.route("/api/runs/<run_id>/requirements-conversation", methods=["GET"])
def get_requirements_conversation(run_id: str):
    """Return (or lazily initialize) the requirements conversation for a run.

    Safe for older runs — if the run predates this system, init_requirements_conversation
    lazily creates draft + questions from whatever context is available, or returns a
    not_applicable state for read-only workflows (bugfix, inventory, etc.).
    """
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    state = load_state(run_id)
    if state is None:
        abort(404, f"Run {run_id} state not found")

    conversation = req_conv_mod.lazy_init_from_run_state(run_dir, state)
    planning_gate = planning_gate_mod.build_planning_gate_from_run_state(state, run_dir=run_dir)
    unanswered = req_conv_mod.get_unanswered_required(conversation)
    return jsonify({
        "run_id": run_id,
        "conversation": conversation,
        "planning_gate": planning_gate,
        "can_approve": len(unanswered) == 0 and not conversation.get("requirements_approved"),
        "unanswered_required": unanswered,
    })


@app.route("/api/runs/<run_id>/requirements-conversation/answer", methods=["POST"])
def save_requirements_answer(run_id: str):
    """Save one question answer in requirements_questions.json and conversation transcript."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    body = request.get_json(silent=True) or {}
    question_id = (body.get("question_id") or "").strip()
    if not question_id:
        abort(400, "question_id is required")
    answer = body.get("answer")
    freeform_answer = (body.get("freeform_answer") or "").strip()

    # Lazy-init if needed so the endpoint works even when the client calls it
    # before explicitly opening the conversation view.
    state = load_state(run_id)
    if state is None:
        abort(404, f"Run {run_id} state not found")
    req_conv_mod.lazy_init_from_run_state(run_dir, state)

    try:
        updated = req_conv_mod.save_answer(run_dir, question_id, answer, freeform_answer)
    except ValueError as exc:
        abort(400, str(exc))

    unanswered = req_conv_mod.get_unanswered_required(updated)
    return jsonify({
        "run_id": run_id,
        "conversation": updated,
        "can_approve": len(unanswered) == 0 and not updated.get("requirements_approved"),
        "unanswered_required": unanswered,
    })


@app.route("/api/runs/<run_id>/requirements-conversation/approve", methods=["POST"])
def approve_requirements(run_id: str):
    """Approve requirements: merge draft + answers → approved_requirements.md + signoff.

    Does NOT automatically start architecture conversation or build. Returns the
    updated planning_gate so the UI can refresh the Planning Gate card.
    """
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    state = load_state(run_id)
    if state is None:
        abort(404, f"Run {run_id} state not found")

    result = req_conv_mod.approve_requirements(run_dir)
    if not result["approved"]:
        abort(400, result.get("error") or "Approval failed")

    # Re-read planning gate from updated artifacts
    planning_gate = planning_gate_mod.build_planning_gate_from_run_state(state, run_dir=run_dir)
    return jsonify({
        "run_id": run_id,
        "approved": True,
        "conversation": result["state"],
        "planning_gate": planning_gate,
    })


# ── Architecture Conversation ─────────────────────────────────────────────────

@app.route("/api/runs/<run_id>/architecture-conversation", methods=["GET"])
def get_architecture_conversation(run_id: str):
    """Return (or lazily initialize) the architecture conversation for a run.

    Returns can_start=False with a blocking_reason when requirements are not yet
    approved. Safe for older runs — gracefully degrades to a not_started state.
    """
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    state = load_state(run_id)
    if state is None:
        abort(404, f"Run {run_id} state not found")

    conversation = arch_conv_mod.lazy_init_from_run_state(run_dir, state)
    can_start = conversation.get("can_start", True)
    blocking_reason = conversation.get("blocking_reason")

    # can_start defaults to True for initialized conversations
    if "can_start" not in conversation:
        ok, br = arch_conv_mod.can_start_architecture(run_dir)
        can_start = ok
        blocking_reason = br

    planning_gate = planning_gate_mod.build_planning_gate_from_run_state(state, run_dir=run_dir)
    unanswered = arch_conv_mod.get_unanswered_required(conversation)
    return jsonify({
        "run_id": run_id,
        "conversation": conversation,
        "planning_gate": planning_gate,
        "can_start": can_start,
        "blocking_reason": blocking_reason,
        "can_approve": len(unanswered) == 0 and not conversation.get("architecture_approved"),
        "unanswered_required": unanswered,
    })


@app.route("/api/runs/<run_id>/architecture-conversation/answer", methods=["POST"])
def save_architecture_answer(run_id: str):
    """Save one architecture question answer."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    body = request.get_json(silent=True) or {}
    question_id = (body.get("question_id") or "").strip()
    if not question_id:
        abort(400, "question_id is required")
    answer = body.get("answer")
    freeform_answer = (body.get("freeform_answer") or "").strip()

    state = load_state(run_id)
    if state is None:
        abort(404, f"Run {run_id} state not found")

    # Lazy-init if needed
    arch_conv_mod.lazy_init_from_run_state(run_dir, state)

    try:
        updated = arch_conv_mod.save_answer(run_dir, question_id, answer, freeform_answer)
    except ValueError as exc:
        abort(400, str(exc))

    unanswered = arch_conv_mod.get_unanswered_required(updated)
    return jsonify({
        "run_id": run_id,
        "conversation": updated,
        "can_approve": len(unanswered) == 0 and not updated.get("architecture_approved"),
        "unanswered_required": unanswered,
    })


@app.route("/api/runs/<run_id>/architecture-conversation/approve", methods=["POST"])
def approve_architecture(run_id: str):
    """Approve architecture: merge draft + answers → approved_architecture.md + signoff.

    Does NOT generate GLOBAL_INSTRUCTIONS.md or start build. Returns updated
    planning_gate so the UI can refresh the Planning Gate card.
    """
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    state = load_state(run_id)
    if state is None:
        abort(404, f"Run {run_id} state not found")

    result = arch_conv_mod.approve_architecture(run_dir)
    if not result["approved"]:
        abort(400, result.get("error") or "Architecture approval failed")

    planning_gate = planning_gate_mod.build_planning_gate_from_run_state(state, run_dir=run_dir)
    return jsonify({
        "run_id": run_id,
        "approved": True,
        "conversation": result["state"],
        "planning_gate": planning_gate,
    })


# ── Global Instructions ────────────────────────────────────────────────────────

@app.route("/api/runs/<run_id>/global-instructions", methods=["GET"])
def get_global_instructions(run_id: str):
    """Return global instructions status: existence, gate checks, blocking reasons."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    state = load_state(run_id)
    status = gi_mod.get_global_instructions_status(run_dir)
    planning_gate = planning_gate_mod.build_planning_gate_from_run_state(
        state or {}, run_dir=run_dir
    )
    return jsonify({"run_id": run_id, "planning_gate": planning_gate, **status})


@app.route("/api/runs/<run_id>/global-instructions/generate-requirements", methods=["POST"])
def generate_requirements_md(run_id: str):
    """Generate requirements.md from approved requirements artifacts."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    result = gi_mod.generate_requirements_md(run_dir)
    if not result["success"]:
        abort(400, result.get("error") or "Could not generate requirements.md")
    state = load_state(run_id)
    status = gi_mod.get_global_instructions_status(run_dir)
    planning_gate = planning_gate_mod.build_planning_gate_from_run_state(
        state or {}, run_dir=run_dir
    )
    return jsonify({
        "run_id": run_id,
        "artifact": result["artifact"],
        "planning_gate": planning_gate,
        **status,
    })


@app.route("/api/runs/<run_id>/global-instructions/generate", methods=["POST"])
def generate_global_instructions(run_id: str):
    """Generate GLOBAL_INSTRUCTIONS.md + global_instructions_state.json."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        abort(404, f"Run {run_id} not found")
    state = load_state(run_id)
    existing_app = (state or {}).get("existing_app")
    result = gi_mod.generate_global_instructions(run_dir, existing_app_path=existing_app)
    if not result["success"]:
        abort(400, result.get("error") or "Could not generate GLOBAL_INSTRUCTIONS.md")
    gi_status = gi_mod.get_global_instructions_status(run_dir)
    planning_gate = planning_gate_mod.build_planning_gate_from_run_state(
        state or {}, run_dir=run_dir
    )
    return jsonify({
        "run_id": run_id,
        "artifacts": result["artifacts"],
        "planning_gate": planning_gate,
        **gi_status,
    })


# ── Local Delivery + Optional Sandbox Push ──────────────────────────────────
# The repo path for delivery is NEVER taken from the client — it is always read
# from this run's own run_state.json (existing_app), which was set when the run
# was created. This is what stops the frontend from being able to point the
# backend at an arbitrary path, branch, or remote.

def _delivery_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id / "delivery"


DELIVERY_ARTIFACT_FILES = (
    "delivery_safety_check.md",
    "delivery_state.json",
    "github_delivery_plan.md",
    "repo_hygiene_report.md",
    "repo_hygiene_report.json",
)


def _load_delivery_state(run_id: str) -> dict | None:
    state_path = _delivery_dir(run_id) / "delivery_state.json"
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"decision": "UNKNOWN"}


def _delivery_artifacts(run_id: str) -> tuple[list[str], dict[str, bool]]:
    ddir = _delivery_dir(run_id)
    artifacts = [f.name for f in ddir.iterdir() if f.is_file()] if ddir.exists() else []
    artifact_set = set(artifacts)
    availability = {name: name in artifact_set for name in DELIVERY_ARTIFACT_FILES}
    return sorted(artifacts), availability


def _delivery_repo_for_run(run_id: str) -> str | None:
    state = load_state(run_id)
    delivery_state = _load_delivery_state(run_id) or {}
    if not state:
        return delivery_state.get("repo_path")
    return (
        state.get("existing_app")
        or state.get("existing_app_path")
        or state.get("delivery_repo")
        or state.get("delivery_repo_path")
        or delivery_state.get("repo_path")
    )


def _boundary_info_for_run(run_id: str) -> dict:
    """Selected Feature Change Boundary status, read from this run's own run_state.json
    (written by pipeline_existing_app_upgrade) — never recomputed by the backend."""
    run_state = load_state(run_id) or {}
    return {
        "status": run_state.get("change_boundary_status"),
        "violation_count": run_state.get("boundary_violation_count"),
        "out_of_scope_review_findings": run_state.get("out_of_scope_review_findings"),
        "blocked": bool(run_state.get("local_delivery_blocked_by_boundary")),
    }


def _smoke_mutation_info_for_run(run_id: str) -> dict:
    """Smoke Mutation status, read from this run's own run_state.json (written by
    pipeline_existing_app_upgrade / pipeline_continue_feature_sprint after smoke checks
    run) — never recomputed by the backend. Tells the UI whether smoke-check commands
    (e.g. `npm install`) mutated a tracked file, separately from the build's own change
    boundary, so a lockfile rewrite is never attributed to the Claude build."""
    run_state = load_state(run_id) or {}
    return {
        "status": run_state.get("smoke_mutation_status"),
        "file_count": run_state.get("smoke_mutation_file_count"),
        "blocked": bool(run_state.get("smoke_mutation_blocked_delivery")),
    }


@app.route("/api/runs/<run_id>/delivery", methods=["GET"])
def get_delivery_state(run_id: str):
    if load_state(run_id) is None:
        abort(404, f"Run {run_id} not found")
    repo_path = _delivery_repo_for_run(run_id)
    boundary = _boundary_info_for_run(run_id)
    smoke_mutation = _smoke_mutation_info_for_run(run_id)
    state = _load_delivery_state(run_id)
    artifacts, artifact_availability = _delivery_artifacts(run_id)
    has_delivery_artifacts = bool(artifacts)

    if state and state.get("repo_path"):
        repo_path = repo_path or state.get("repo_path")

    if not repo_path and not has_delivery_artifacts:
        return jsonify({
            "available": False,
            "reason": "This run has no associated git repo (not an Existing App Upgrade run).",
            "state": None,
            "artifacts": artifacts,
            "artifact_availability": artifact_availability,
            "boundary": boundary,
            "smoke_mutation": smoke_mutation,
        })

    return jsonify({
        "available": True,
        "repo_path": repo_path,
        "state": state,
        "artifacts": artifacts,
        "artifact_availability": artifact_availability,
        "boundary": boundary,
        "smoke_mutation": smoke_mutation,
    })


@app.route("/api/runs/<run_id>/delivery/precheck", methods=["GET"])
def get_delivery_precheck(run_id: str):
    """Read-only safety check preview — never modifies the repo."""
    if load_state(run_id) is None:
        abort(404, f"Run {run_id} not found")
    repo_path = _delivery_repo_for_run(run_id)
    if not repo_path:
        abort(400, "This run has no associated git repo")

    mode = "sandbox_push" if request.args.get("sandbox_push") == "true" else "local_only"
    branch_name = request.args.get("branch_name") or None
    try:
        precheck = delivery_mod.assert_clean_delivery_preconditions(repo_path, mode, branch_name)
    except delivery_mod.DeliveryError as e:
        abort(400, str(e))
    return jsonify(precheck)


@app.route("/api/runs/<run_id>/delivery/artifacts/<filename>", methods=["GET"])
def get_delivery_artifact(run_id: str, filename: str):
    if ".." in filename or "/" in filename:
        abort(400, "Invalid filename")
    p = _delivery_dir(run_id) / filename
    if not p.exists():
        abort(404, f"{filename} not found in {run_id}/delivery")
    return jsonify({"run_id": run_id, "filename": filename, "content": p.read_text(encoding="utf-8", errors="replace")})


def _run_delivery_action(run_id: str, mode: str):
    run_state = load_state(run_id)
    if run_state is None:
        abort(404, f"Run {run_id} not found")
    repo_path = _delivery_repo_for_run(run_id)
    if not repo_path:
        abort(400, "This run has no associated git repo")
    if run_state.get("local_delivery_blocked_by_boundary"):
        reasons = []
        if run_state.get("change_boundary_status") == "FAIL":
            reasons.append("a Selected Feature Change Boundary violation (files outside the selected "
                           "sprint were changed or deleted — see boundary_violation_report.md)")
        if run_state.get("smoke_mutation_blocked_delivery"):
            reasons.append("a smoke-check-induced mutation of tracked files outside the selected "
                           "feature boundary, caused by a smoke-check command (e.g. `npm install`), "
                           "not the Claude build — see smoke_mutation_report.md")
        detail = " and ".join(reasons) or "a safety check failure"
        abort(409, f"Local Delivery is blocked: {detail} was detected for this run. No branch, "
                    "commit, or push was performed.")

    body = request.get_json(force=True, silent=True) or {}
    branch_name = (body.get("branch_name") or "").strip()
    commit_message = (body.get("commit_message") or "").strip()
    if not branch_name or not commit_message:
        abort(400, "branch_name and commit_message are required")

    allowlist = set(delivery_mod.DEFAULT_SANDBOX_ALLOWLIST)
    extra = body.get("allow_sandbox_remote") or []
    if isinstance(extra, list):
        allowlist |= {str(x).strip() for x in extra if str(x).strip()}

    try:
        state = delivery_mod.run_local_delivery(
            repo_path, mode=mode, branch_name=branch_name, commit_message=commit_message,
            output_dir=_delivery_dir(run_id), sandbox_allowlist=allowlist,
        )
    except delivery_mod.DeliveryError as e:
        abort(400, str(e))
    return jsonify(state)


@app.route("/api/runs/<run_id>/delivery/commit", methods=["POST"])
def create_delivery_commit(run_id: str):
    """Local-only: create a branch + commit. Never pushes, regardless of request body."""
    return _run_delivery_action(run_id, mode="local_only")


@app.route("/api/runs/<run_id>/delivery/push", methods=["POST"])
def push_delivery_sandbox(run_id: str):
    """Sandbox push attempt. Only actually pushes if every safety precondition passes —
    company repos, protected branches, and non-allowlisted remotes are blocked inside
    delivery_mod.run_local_delivery regardless of this request."""
    return _run_delivery_action(run_id, mode="sandbox_push")


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5001)
