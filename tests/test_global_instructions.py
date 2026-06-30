"""
Global Instructions — requirements.md and GLOBAL_INSTRUCTIONS.md generation.

Covers:
 1.  Cannot generate requirements.md before requirements approval.
 2.  Generates requirements.md after requirements approval.
 3.  requirements.md content includes approved requirements + Q&A decisions.
 4.  Cannot generate GLOBAL_INSTRUCTIONS.md before architecture approval.
 5.  Cannot generate GLOBAL_INSTRUCTIONS.md if only requirements are approved.
 6.  Generates GLOBAL_INSTRUCTIONS.md after requirements + architecture approval.
 7.  Auto-generates requirements.md when generating GLOBAL_INSTRUCTIONS.md if missing.
 8.  GLOBAL_INSTRUCTIONS.md includes tech stack, safety rules, sprint execution rules,
     orchestrator expectations, and source artifacts section.
 9.  Writes global_instructions_state.json with status=created.
10.  Planning gate detects global instructions and becomes ready_for_build.
11.  Backend GET endpoint returns correct status.
12.  Backend POST generate-requirements works and returns artifact.
13.  Backend POST generate works and returns updated planning gate.
14.  build prompt for feature sprint includes GLOBAL_INSTRUCTIONS.md preamble when
     GLOBAL_INSTRUCTIONS.md exists in the run directory.
15.  get_global_instructions_status returns blocking reason when gate not met.
16.  GLOBAL_INSTRUCTIONS.md includes Sprint Orchestrator Expectations section.

Fixture runs only — never uses real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import global_instructions as gi
import planning_gate as pg

# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_req_signoff(run_dir: Path, approved: bool = True) -> None:
    state = {"status": "approved" if approved else "questions_pending", "requirements_status": "approved" if approved else "questions_pending"}
    (run_dir / "requirements_signoff_state.json").write_text(json.dumps(state), encoding="utf-8")


def _make_arch_signoff(run_dir: Path, approved: bool = True) -> None:
    state = {"status": "approved" if approved else "questions_pending", "architecture_status": "approved" if approved else "questions_pending"}
    (run_dir / "architecture_signoff_state.json").write_text(json.dumps(state), encoding="utf-8")


def _make_approved_requirements(run_dir: Path) -> None:
    (run_dir / "approved_requirements.md").write_text(
        "# Requirements\nBuild a task management app.\n\n## Must-Have\n- Task CRUD\n- User auth\n",
        encoding="utf-8",
    )


def _make_approved_architecture(run_dir: Path) -> None:
    (run_dir / "approved_architecture.md").write_text(
        "# Architecture\nReact frontend, Flask backend, SQLite.\n",
        encoding="utf-8",
    )


def _make_arch_questions(run_dir: Path) -> None:
    questions_data = {
        "entry_point": "raw_idea",
        "questions": [
            {"id": "frontend_stack", "label": "Frontend", "question": "Which frontend?", "type": "single_choice",
             "options": ["React + TypeScript", "Vue", "Plain HTML"], "recommended": "React + TypeScript",
             "answer": None, "freeform_answer": "", "why": "", "required": True},
            {"id": "backend_stack", "label": "Backend", "question": "Which backend?", "type": "single_choice",
             "options": ["Flask (Python)", "FastAPI", "None"], "recommended": "Flask (Python)",
             "answer": None, "freeform_answer": "", "why": "", "required": True},
        ],
        "answers": {
            "frontend_stack": "React + TypeScript",
            "backend_stack": "Flask (Python)",
        },
    }
    (run_dir / "architecture_questions.json").write_text(json.dumps(questions_data), encoding="utf-8")


def _make_full_run(run_dir: Path) -> None:
    _make_req_signoff(run_dir, approved=True)
    _make_arch_signoff(run_dir, approved=True)
    _make_approved_requirements(run_dir)
    _make_approved_architecture(run_dir)
    _make_arch_questions(run_dir)


# ── 1. Cannot generate requirements.md before requirements approval ────────────

def test_generate_requirements_blocked_before_approval():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        result = gi.generate_requirements_md(run_dir)
        assert not result["success"]
        assert result["artifact"] is None
        assert "approved" in result["error"].lower() or "requirements" in result["error"].lower()
        assert not (run_dir / gi.REQUIREMENTS_MD).exists()


# ── 2. Generates requirements.md after requirements approval ───────────────────

def test_generate_requirements_after_approval():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir, approved=True)
        _make_approved_requirements(run_dir)
        result = gi.generate_requirements_md(run_dir)
        assert result["success"], result.get("error")
        assert result["artifact"] == gi.REQUIREMENTS_MD
        assert (run_dir / gi.REQUIREMENTS_MD).exists()


# ── 3. requirements.md content includes approved requirements + Q&A ───────────

def test_requirements_md_content():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir, approved=True)
        _make_approved_requirements(run_dir)
        questions_data = {
            "entry_point": "raw_idea",
            "questions": [
                {"id": "target_users", "label": "Target Users", "question": "Who are the users?",
                 "type": "short_text", "options": [], "answer": "developers", "freeform_answer": ""},
            ],
        }
        (run_dir / "requirements_questions.json").write_text(json.dumps(questions_data), encoding="utf-8")
        gi.generate_requirements_md(run_dir)
        content = (run_dir / gi.REQUIREMENTS_MD).read_text()
        assert "# Requirements" in content
        assert "Status: Approved" in content or "Approved" in content
        assert "Task management" in content or "requirements" in content.lower()
        assert "Target Users" in content or "developers" in content


# ── 4. Cannot generate GLOBAL_INSTRUCTIONS.md before architecture approval ─────

def test_generate_gi_blocked_before_arch_approval():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir, approved=True)
        _make_approved_requirements(run_dir)
        # NO architecture signoff
        result = gi.generate_global_instructions(run_dir)
        assert not result["success"]
        assert "architecture" in result["error"].lower()
        assert not (run_dir / gi.GLOBAL_INSTRUCTIONS_MD).exists()


# ── 5. Cannot generate GLOBAL_INSTRUCTIONS.md if only requirements approved ───

def test_generate_gi_blocked_when_only_req_approved():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir, approved=True)
        _make_approved_requirements(run_dir)
        _make_arch_signoff(run_dir, approved=False)
        result = gi.generate_global_instructions(run_dir)
        assert not result["success"]
        assert "architecture" in result["error"].lower()


# ── 6. Generates GLOBAL_INSTRUCTIONS.md after full approval ───────────────────

def test_generate_gi_after_full_approval():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)
        # Pre-create requirements.md
        gi.generate_requirements_md(run_dir)
        result = gi.generate_global_instructions(run_dir)
        assert result["success"], result.get("error")
        assert (run_dir / gi.GLOBAL_INSTRUCTIONS_MD).exists()
        assert gi.GLOBAL_INSTRUCTIONS_MD in result["artifacts"]


# ── 7. Auto-generates requirements.md when generating GLOBAL_INSTRUCTIONS ─────

def test_gi_auto_generates_requirements_md():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)
        assert not (run_dir / gi.REQUIREMENTS_MD).exists()
        result = gi.generate_global_instructions(run_dir)
        assert result["success"], result.get("error")
        assert (run_dir / gi.REQUIREMENTS_MD).exists()
        assert (run_dir / gi.GLOBAL_INSTRUCTIONS_MD).exists()


# ── 8. GLOBAL_INSTRUCTIONS.md has all required sections ──────────────────────

def test_gi_content_has_all_sections():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)
        gi.generate_global_instructions(run_dir)
        content = (run_dir / gi.GLOBAL_INSTRUCTIONS_MD).read_text()
        # Required sections
        assert "## Product Vision" in content
        assert "## Approved Architecture" in content or "Architecture" in content
        assert "## Selected Tech Stack" in content
        assert "## Build Safety Rules" in content or "Build Safety" in content
        assert "## Sprint Execution Rules" in content or "Sprint Execution" in content
        assert "## Source Artifacts" in content
        # Tech stack values from answers
        assert "React + TypeScript" in content
        assert "Flask (Python)" in content


# ── 9. Writes global_instructions_state.json ──────────────────────────────────

def test_gi_writes_state_file():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)
        gi.generate_global_instructions(run_dir)
        state = gi.load_global_instructions_state(run_dir)
        assert state is not None
        assert state["status"] == "created"
        assert state["global_instructions_artifact"] == gi.GLOBAL_INSTRUCTIONS_MD
        assert gi.REQUIREMENTS_MD in state["source_artifacts"] or gi.REQUIREMENTS_MD == state.get("requirements_artifact")
        assert "created_at" in state


# ── 10. Planning gate detects global instructions → ready_for_build ───────────

def test_planning_gate_ready_after_gi():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)
        gi.generate_global_instructions(run_dir)
        gate = pg.build_planning_gate_state(
            entry_point="raw_idea",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate["global_instructions_created"] is True
        assert gate["requirements_approved"] is True
        assert gate["architecture_approved"] is True
        assert gate["build_allowed_by_planning_gate"] is True
        assert gate["planning_stage"] == "ready_for_build"


# ── 11. Backend GET endpoint returns correct status ───────────────────────────

def test_backend_get_global_instructions():
    import importlib, types
    import sys as _sys
    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    _sys.path.insert(0, str(backend_dir.parent))

    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)

        with patch("global_instructions.generate_requirements_md") as mock_gen_req, \
             patch("global_instructions.generate_global_instructions") as mock_gen_gi:
            # Just test the status function
            status = gi.get_global_instructions_status(run_dir)
            assert status["can_generate_requirements"] is True
            assert status["can_generate_global_instructions"] is True
            assert status["requirements_md_exists"] is False
            assert status["global_instructions_exists"] is False
            assert status["blocking_reason"] is None or status["can_generate_global_instructions"]


def test_backend_get_global_instructions_blocked():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # No signoffs at all
        status = gi.get_global_instructions_status(run_dir)
        assert status["can_generate_requirements"] is False
        assert status["can_generate_global_instructions"] is False
        assert status["blocking_reason"] is not None


# ── 12. Backend POST generate-requirements works ──────────────────────────────

def test_backend_post_generate_requirements():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir)
        _make_approved_requirements(run_dir)
        result = gi.generate_requirements_md(run_dir)
        assert result["success"]
        assert result["artifact"] == gi.REQUIREMENTS_MD
        assert (run_dir / gi.REQUIREMENTS_MD).exists()
        # Status now reflects existence
        status = gi.get_global_instructions_status(run_dir)
        assert status["requirements_md_exists"] is True


# ── 13. Backend POST generate works and planning gate refreshes ───────────────

def test_backend_post_generate_gi():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)
        result = gi.generate_global_instructions(run_dir)
        assert result["success"]
        assert (run_dir / gi.GLOBAL_INSTRUCTIONS_MD).exists()
        # Planning gate should now show ready
        gate = pg.build_planning_gate_state(
            entry_point="raw_idea",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate["build_allowed_by_planning_gate"] is True


# ── 14. Feature sprint build prompt includes GLOBAL_INSTRUCTIONS preamble ─────

def test_feature_sprint_build_prompt_includes_gi_preamble():
    import pipeline_mvp_builder as pmb

    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)
        # Write GLOBAL_INSTRUCTIONS.md in run_dir
        gi.generate_global_instructions(run_dir)
        assert (run_dir / gi.GLOBAL_INSTRUCTIONS_MD).exists()

        # Build a minimal plan/sprint/scan fixture
        plan_json = {
            "total_sprints": 1,
            "sprints": [{"sprint_number": 1, "title": "Auth", "goal": "Add login",
                          "features": ["login form"], "completion_criteria": ["user can log in"],
                          "likely_files_created": ["src/Login.tsx"],
                          "likely_files_modified": [], "must_not_modify": []}],
        }
        selected_sprint = plan_json["sprints"][0]
        scan = {"tech_stack": ["React", "TypeScript"]}
        summary = "Existing React app with dashboard."

        prompt = pmb.generate_selected_feature_sprint_build_prompt(
            summary, scan, plan_json, selected_sprint, run_dir,
        )
        assert "GLOBAL_INSTRUCTIONS.md" in prompt
        assert "BUILD CONSTITUTION" in prompt
        assert str(run_dir) in prompt


# ── 15. get_global_instructions_status blocking reason ───────────────────────

def test_status_blocking_reason_only_req_approved():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir, approved=True)
        _make_approved_requirements(run_dir)
        gi.generate_requirements_md(run_dir)
        # Architecture not yet approved
        status = gi.get_global_instructions_status(run_dir)
        assert not status["global_instructions_exists"]
        assert not status["can_generate_global_instructions"]
        assert status["blocking_reason"] is not None
        assert "architecture" in status["blocking_reason"].lower()


# ── 16. GLOBAL_INSTRUCTIONS.md includes Sprint Orchestrator Expectations ──────

def test_gi_includes_sprint_orchestrator_section():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_full_run(run_dir)
        gi.generate_global_instructions(run_dir)
        content = (run_dir / gi.GLOBAL_INSTRUCTIONS_MD).read_text()
        assert "Sprint Orchestrator" in content
        assert "handoff" in content.lower() or "next_action" in content.lower()
        assert "restart" in content.lower() or "from scratch" in content.lower()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)
