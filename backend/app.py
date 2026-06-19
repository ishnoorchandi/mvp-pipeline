"""
MVP Pipeline — Backend API
===========================
Serves run data from the runs/ folder.
Runs are file-based (no DB yet).

Routes:
  POST /api/runs                         → start a new pipeline run (async)
  GET  /api/runs                         → list all runs + status
  GET  /api/runs/<run_id>                → full run state + artifact list
  GET  /api/runs/<run_id>/artifacts/<filename> → get raw artifact content
  GET  /health                           → health check
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_state(run_id: str) -> dict | None:
    p = RUNS_DIR / run_id / "run_state.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


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
        })
    return runs


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

    # Pre-allocate a run ID
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted([
        d.name for d in RUNS_DIR.iterdir()
        if d.is_dir() and d.name.startswith("run_")
    ])
    last_num = int(existing[-1].split("_")[1]) if existing else 0
    run_id = f"run_{last_num + 1:03d}"

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
    return jsonify(state)


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


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5001)
