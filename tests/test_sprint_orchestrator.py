"""
Sprint Orchestrator — persistent sprint state manager.

Covers:
 1.  Cannot initialize if planning gate blocks (requirements not approved).
 2.  Cannot initialize if requirements.md is missing.
 3.  Cannot initialize if GLOBAL_INSTRUCTIONS.md is missing.
 4.  Cannot initialize if approved_architecture.md is missing.
 5.  Initialize writes sprint_orchestrator_state.json.
 6.  Initialize is idempotent for same sprint.
 7.  Different active sprint returns ValueError (conflict).
 8.  Selected sprint metadata is recorded from feature_sprint_plan.json.
 9.  Sprint not build-ready is blocked.
10.  Recording build attempt updates phase/next_action.
11.  Smoke failure leads to fix-prompt next_action.
12.  Smoke pass leads to review pending.
13.  Review pass leads to governance pending.
14.  Governance pass after smoke/review pass leads to ready_for_completion.
15.  Waived checks contribute to ready_for_completion.
16.  Handoff includes all required fields.
17.  Handoff generation writes sprint_<n>_handoff.md.
18.  State is resumable from disk.
19.  Backend GET/init/generate-handoff endpoints respond correctly.
20.  Backend record endpoints update state.
21.  compute_next_action is correct for all phases.

Fixture runs only — never uses real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sprint_orchestrator as so
import planning_gate as pg


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_req_signoff(run_dir: Path) -> None:
    (run_dir / "requirements_signoff_state.json").write_text(
        json.dumps({"status": "approved"}), encoding="utf-8"
    )


def _make_arch_signoff(run_dir: Path) -> None:
    (run_dir / "architecture_signoff_state.json").write_text(
        json.dumps({"status": "approved"}), encoding="utf-8"
    )


def _make_approved_req(run_dir: Path) -> None:
    (run_dir / "approved_requirements.md").write_text("# Requirements\n", encoding="utf-8")


def _make_approved_arch(run_dir: Path) -> None:
    (run_dir / "approved_architecture.md").write_text("# Architecture\n", encoding="utf-8")


def _make_requirements_md(run_dir: Path) -> None:
    (run_dir / "requirements.md").write_text("# Requirements\n", encoding="utf-8")


def _make_global_instructions(run_dir: Path) -> None:
    (run_dir / "GLOBAL_INSTRUCTIONS.md").write_text("# GLOBAL_INSTRUCTIONS\n", encoding="utf-8")


def _make_sprint_plan(run_dir: Path, build_ready: bool = True) -> None:
    plan = {
        "total_sprints": 2,
        "sprints": [
            {
                "sprint_number": 1,
                "title": "Dashboard Sprint",
                "goal": "Build the main dashboard",
                "features": ["Dashboard view"],
                "likely_files_created": ["src/Dashboard.tsx"],
                "likely_files_modified": [],
                "must_not_modify": [],
                "quality": {
                    "build_ready": build_ready,
                    "risk_level": "medium",
                    "quality_score": 84,
                },
            },
            {
                "sprint_number": 2,
                "title": "Auth Sprint",
                "goal": "Add authentication",
                "features": ["Login form"],
                "likely_files_created": ["src/Login.tsx"],
                "likely_files_modified": [],
                "must_not_modify": [],
                "quality": {
                    "build_ready": True,
                    "risk_level": "low",
                    "quality_score": 90,
                },
            },
        ],
    }
    (run_dir / "feature_sprint_plan.json").write_text(json.dumps(plan), encoding="utf-8")


def _make_run_state(run_dir: Path) -> None:
    state = {"entry_point": "existing_app_upgrade", "execution_mode": "build"}
    (run_dir / "run_state.json").write_text(json.dumps(state), encoding="utf-8")


def _full_run(run_dir: Path, build_ready: bool = True) -> None:
    _make_req_signoff(run_dir)
    _make_arch_signoff(run_dir)
    _make_approved_req(run_dir)
    _make_approved_arch(run_dir)
    _make_requirements_md(run_dir)
    _make_global_instructions(run_dir)
    _make_sprint_plan(run_dir, build_ready=build_ready)
    _make_run_state(run_dir)


# ── 1. Planning gate blocks init ───────────────────────────────────────────────

def test_init_blocked_by_planning_gate():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # No signoffs — planning gate will block
        _make_requirements_md(run_dir)
        _make_global_instructions(run_dir)
        _make_approved_arch(run_dir)
        _make_run_state(run_dir)
        try:
            so.initialize_orchestrator(run_dir, 1)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "blocked" in str(e).lower() or "planning" in str(e).lower()


# ── 2. requirements.md missing ────────────────────────────────────────────────

def test_init_blocked_requirements_md_missing():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir)
        _make_arch_signoff(run_dir)
        _make_approved_req(run_dir)
        _make_approved_arch(run_dir)
        # NO requirements.md
        _make_global_instructions(run_dir)
        _make_run_state(run_dir)
        try:
            so.initialize_orchestrator(run_dir, 1)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "requirements.md" in str(e)


# ── 3. GLOBAL_INSTRUCTIONS.md missing ────────────────────────────────────────

def test_init_blocked_global_instructions_missing():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir)
        _make_arch_signoff(run_dir)
        _make_approved_req(run_dir)
        _make_approved_arch(run_dir)
        _make_requirements_md(run_dir)
        # NO GLOBAL_INSTRUCTIONS.md
        _make_run_state(run_dir)
        try:
            so.initialize_orchestrator(run_dir, 1)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "GLOBAL_INSTRUCTIONS.md" in str(e)


# ── 4. approved_architecture.md missing ──────────────────────────────────────

def test_init_blocked_approved_arch_missing():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _make_req_signoff(run_dir)
        _make_arch_signoff(run_dir)
        _make_approved_req(run_dir)
        # NO approved_architecture.md
        _make_requirements_md(run_dir)
        _make_global_instructions(run_dir)
        _make_run_state(run_dir)
        try:
            so.initialize_orchestrator(run_dir, 1)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "approved_architecture.md" in str(e)


# ── 5. Initialize writes state file ──────────────────────────────────────────

def test_init_writes_state_file():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        state = so.initialize_orchestrator(run_dir, 1)
        assert (run_dir / so.STATE_FILE).exists()
        assert state["active_sprint"] == 1
        assert state["status"] == so.STATUS_ACTIVE
        assert state["current_phase"] == so.PHASE_INITIALIZED


# ── 6. Initialize is idempotent for same sprint ───────────────────────────────

def test_init_idempotent_same_sprint():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        state1 = so.initialize_orchestrator(run_dir, 1)
        state2 = so.initialize_orchestrator(run_dir, 1)
        assert state1["created_at"] == state2["created_at"]
        assert state2["active_sprint"] == 1


# ── 7. Different active sprint raises ValueError ──────────────────────────────

def test_init_conflicts_with_different_active_sprint():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        try:
            so.initialize_orchestrator(run_dir, 2)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "already active" in str(e).lower() or "sprint 1" in str(e).lower()


# ── 8. Sprint metadata recorded from feature_sprint_plan.json ─────────────────

def test_init_records_sprint_metadata():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        state = so.initialize_orchestrator(run_dir, 1)
        assert state.get("sprint_title") == "Dashboard Sprint"
        assert state.get("sprint_goal") == "Build the main dashboard"
        quality = state.get("sprint_quality") or {}
        assert quality.get("build_ready") is True
        assert quality.get("risk_level") == "medium"


# ── 9. Non-build-ready sprint is blocked ─────────────────────────────────────

def test_init_blocked_sprint_not_build_ready():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir, build_ready=False)
        try:
            so.initialize_orchestrator(run_dir, 1)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "decomposition" in str(e).lower() or "build-ready" in str(e).lower() or "not build-ready" in str(e).lower()


# ── 10. Build attempt updates phase and next_action ──────────────────────────

def test_record_build_attempt_completed():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        state = so.record_build_attempt(run_dir, "completed", summary="Build done.")
        assert state["current_phase"] == so.PHASE_SMOKE_PENDING
        assert "smoke" in state["next_action"].lower()
        assert len(state["attempts"]) == 1
        assert state["attempts"][0]["status"] == "completed"


def test_record_build_attempt_failed():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        state = so.record_build_attempt(run_dir, "failed", summary="Build errored.")
        assert "fix" in state["next_action"].lower() or "fail" in state["next_action"].lower()


def test_record_build_attempt_interrupted():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        state = so.record_build_attempt(run_dir, "interrupted")
        assert "handoff" in state["next_action"].lower() or "interrupted" in state["next_action"].lower()


# ── 11. Smoke failure → fix next_action ──────────────────────────────────────

def test_smoke_failure_next_action():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        so.record_build_attempt(run_dir, "completed")
        state = so.record_smoke_result(run_dir, "failed", summary="Tests failed.")
        assert state["current_phase"] == so.PHASE_SMOKE_FAILED
        assert "smoke" in state["next_action"].lower() or "fix" in state["next_action"].lower()


# ── 12. Smoke pass → review pending ─────────────────────────────────────────

def test_smoke_pass_leads_to_review_pending():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        so.record_build_attempt(run_dir, "completed")
        state = so.record_smoke_result(run_dir, "passed")
        assert state["current_phase"] == so.PHASE_REVIEW_PENDING
        assert "review" in state["next_action"].lower()


# ── 13. Review pass → governance pending ────────────────────────────────────

def test_review_pass_leads_to_governance_pending():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "passed")
        state = so.record_review_result(run_dir, "passed")
        assert state["current_phase"] == so.PHASE_GOV_PENDING
        assert "governance" in state["next_action"].lower()


# ── 14. All passed → ready_for_completion ────────────────────────────────────

def test_all_checks_pass_ready_for_completion():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "passed")
        so.record_review_result(run_dir, "passed")
        state = so.record_governance_result(run_dir, "passed")
        assert state["status"] == so.STATUS_READY
        assert state["current_phase"] == so.PHASE_READY
        assert "completion" in state["next_action"].lower() or "ready" in state["next_action"].lower()


# ── 15. Waived checks contribute to ready_for_completion ─────────────────────

def test_waived_checks_lead_to_ready_for_completion():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "waived", waived=True, waiver_reason="Smoke waived by user.")
        so.record_review_result(run_dir, "passed")
        state = so.record_governance_result(run_dir, "waived", waived=True, waiver_reason="Governance waived.")
        assert state["status"] == so.STATUS_READY
        assert len(state.get("waivers") or []) >= 1


# ── 16. Handoff includes all required fields ──────────────────────────────────

def test_handoff_content():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "failed", summary="Tests timed out.")
        result = so.generate_handoff(run_dir)
        assert result["success"]
        content = (run_dir / result["artifact"]).read_text()
        assert "requirements.md" in content
        assert "approved_architecture.md" in content
        assert "GLOBAL_INSTRUCTIONS.md" in content
        assert "Sprint 1" in content
        assert "Dashboard Sprint" in content
        assert "smoke_failed" in content or "smoke" in content.lower()
        assert "Next Action" in content
        assert "Continuation Prompt" in content


# ── 17. Handoff generation writes sprint_<n>_handoff.md ──────────────────────

def test_handoff_writes_file():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        result = so.generate_handoff(run_dir)
        assert result["artifact"] == "sprint_1_handoff.md"
        assert (run_dir / "sprint_1_handoff.md").exists()
        # State updated with handoff_artifact
        state = so.load_orchestrator_state(run_dir)
        assert state["handoff_artifact"] == "sprint_1_handoff.md"


# ── 18. State is resumable from disk ─────────────────────────────────────────

def test_state_resumable():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        so.record_build_attempt(run_dir, "completed")
        # Load and verify
        state = so.load_orchestrator_state(run_dir)
        assert state is not None
        assert state["active_sprint"] == 1
        assert state["current_phase"] == so.PHASE_SMOKE_PENDING
        assert len(state["attempts"]) == 1


# ── 19. Backend GET/init/generate-handoff endpoints ──────────────────────────

def test_backend_get_endpoint():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        can_init, blocking = so.can_initialize_orchestration(run_dir)
        assert can_init is True
        assert blocking is None


def test_backend_get_endpoint_blocked():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # No planning approval
        can_init, blocking = so.can_initialize_orchestration(run_dir)
        assert can_init is False
        assert blocking is not None


def test_backend_init_and_handoff():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        state = so.initialize_orchestrator(run_dir, 1)
        assert state["active_sprint"] == 1
        result = so.generate_handoff(run_dir)
        assert result["success"]
        assert (run_dir / result["artifact"]).exists()


# ── 20. Backend record endpoints update state ─────────────────────────────────

def test_backend_record_endpoints():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        # Build attempt
        s1 = so.record_build_attempt(run_dir, "completed", summary="Done.")
        assert s1["current_phase"] == so.PHASE_SMOKE_PENDING
        # Smoke
        s2 = so.record_smoke_result(run_dir, "passed")
        assert s2["current_phase"] == so.PHASE_REVIEW_PENDING
        # Review
        s3 = so.record_review_result(run_dir, "passed")
        assert s3["current_phase"] == so.PHASE_GOV_PENDING
        # Governance
        s4 = so.record_governance_result(run_dir, "passed")
        assert s4["status"] == so.STATUS_READY


# ── 21. compute_next_action is correct for all phases ────────────────────────

def test_compute_next_action_phases():
    def _state(phase, attempt_status=None):
        s = {
            "current_phase": phase,
            "status": "active",
            "attempts": [],
            "blocking_reason": None,
        }
        if attempt_status:
            s["attempts"] = [{"status": attempt_status, "attempt_number": 1}]
        return s

    cases = [
        (so.PHASE_INITIALIZED, "Generate sprint build prompt"),
        (so.PHASE_BUILD_PROMPT_READY, "copy"),
        (so.PHASE_SMOKE_PENDING, "smoke"),
        (so.PHASE_SMOKE_FAILED, "fix"),
        (so.PHASE_REVIEW_PENDING, "review"),
        (so.PHASE_REVIEW_FAILED, "fix"),
        (so.PHASE_GOV_PENDING, "governance"),
        (so.PHASE_GOV_FAILED, "fix"),
        (so.PHASE_READY, "ready for completion"),
        (so.PHASE_COMPLETED, "complete"),
    ]
    for phase, expected_keyword in cases:
        result = so.compute_next_action(_state(phase))
        na = result["next_action"].lower()
        assert expected_keyword.lower() in na, (
            f"Phase {phase}: expected '{expected_keyword}' in '{result['next_action']}'"
        )

    # build_attempted + completed → smoke
    s = _state(so.PHASE_BUILD_ATTEMPTED, attempt_status="completed")
    assert "smoke" in so.compute_next_action(s)["next_action"].lower()

    # build_attempted + failed → fix
    s = _state(so.PHASE_BUILD_ATTEMPTED, attempt_status="failed")
    assert "fix" in so.compute_next_action(s)["next_action"].lower()

    # build_attempted + interrupted → handoff
    s = _state(so.PHASE_BUILD_ATTEMPTED, attempt_status="interrupted")
    assert "handoff" in so.compute_next_action(s)["next_action"].lower()


# ── Issue 2: raw-idea fallback sprint scope ───────────────────────────────────

def _raw_idea_run(run_dir: Path) -> None:
    """Minimal raw-idea run: planning gate approved, three required docs, no sprint plan."""
    _make_req_signoff(run_dir)
    _make_arch_signoff(run_dir)
    _make_approved_req(run_dir)
    _make_approved_arch(run_dir)
    _make_requirements_md(run_dir)
    _make_global_instructions(run_dir)
    (run_dir / "run_state.json").write_text(
        json.dumps({"entry_point": "raw_idea", "execution_mode": "build"}), encoding="utf-8"
    )
    # Deliberately omit feature_sprint_plan.json and selected_feature_sprint_scope.md


def _raw_idea_run_with_arch(run_dir: Path) -> None:
    """Raw idea run with architecture_questions.json providing answered stack choices."""
    _raw_idea_run(run_dir)
    arch_state = {
        "entry_point": "raw_idea",
        "architecture_status": "approved",
        "answers": {
            "frontend_stack": "React + TypeScript",
            "backend_stack": "No backend / frontend-only",
            "data_storage": "Mock data",
            "auth_scope": "No auth in v1",
            "external_services": ["None"],
            "build_workflow": "Sandbox build",
            "first_build_scope": "Frontend-only MVP",
            "deployment_now": "No",
        },
        "questions": [],
    }
    (run_dir / "architecture_questions.json").write_text(json.dumps(arch_state), encoding="utf-8")


# Test 1: raw idea run generates selected_feature_sprint_scope.md
def test_ensure_sprint_scope_creates_artifact():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _raw_idea_run_with_arch(run_dir)
        result = so.ensure_selected_sprint_scope(run_dir, 1, entry_point="raw_idea")
        assert (run_dir / "selected_feature_sprint_scope.md").exists(), (
            "ensure_selected_sprint_scope must create selected_feature_sprint_scope.md"
        )
        assert result["selected_sprint_artifact"] == "selected_feature_sprint_scope.md"
        assert result["sprint_title"] is not None
        assert result["sprint_goal"] is not None


# Test 2: orchestrator state gets non-null sprint_title, sprint_goal, selected_sprint_artifact
def test_raw_idea_init_state_non_null_sprint_fields():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _raw_idea_run_with_arch(run_dir)
        state = so.initialize_orchestrator(run_dir, 1)
        assert state["sprint_title"] is not None, "sprint_title must not be None for raw-idea run"
        assert state["sprint_goal"] is not None, "sprint_goal must not be None for raw-idea run"
        assert state["selected_sprint_artifact"] == "selected_feature_sprint_scope.md", (
            "selected_sprint_artifact must point to fallback scope file"
        )


# Test 3: existing-app upgrade with feature_sprint_plan preserves sprint metadata unchanged
def test_existing_app_upgrade_preserves_sprint_metadata():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)  # includes feature_sprint_plan.json + existing_app_upgrade run_state
        state = so.initialize_orchestrator(run_dir, 1)
        assert state["sprint_title"] == "Dashboard Sprint", (
            "Existing sprint plan title must not be overwritten by fallback"
        )
        assert state["sprint_goal"] == "Build the main dashboard", (
            "Existing sprint plan goal must not be overwritten by fallback"
        )
        assert not (run_dir / "selected_feature_sprint_scope.md").exists(), (
            "ensure_selected_sprint_scope must not run when sprint plan metadata is present"
        )


# Test 4: copy-only next_action does not contain banned phrases
def test_next_action_copy_only_wording():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _raw_idea_run_with_arch(run_dir)
        state = so.initialize_orchestrator(run_dir, 1)
        na = state["next_action"].lower()
        banned = ["start the build", "run the build", "execute the sprint", "launch claude"]
        for phrase in banned:
            assert phrase not in na, (
                f"next_action must not contain '{phrase}'; got: {state['next_action']}"
            )


# Test 5: generated build prompt contains copy-into-Claude instruction
def test_build_prompt_contains_copy_instruction():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _raw_idea_run_with_arch(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        result = so.generate_sprint_build_prompt(run_dir)
        assert result["success"], f"generate_sprint_build_prompt failed: {result.get('error')}"
        artifact_path = run_dir / result["artifact"]
        content = artifact_path.read_text(encoding="utf-8")
        assert "manually" in content.lower() or "copy" in content.lower(), (
            "Build prompt must instruct user to copy it into Claude Code manually"
        )


# Test 6: handoff/continuation prompt says do not restart and implies no auto-execution
def test_handoff_no_restart_no_auto_execution():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _full_run(run_dir)
        so.initialize_orchestrator(run_dir, 1)
        so.record_build_attempt(run_dir, "interrupted", summary="Interrupted mid-build.")
        result = so.generate_handoff(run_dir)
        assert result["success"], f"generate_handoff failed: {result.get('error')}"
        artifact_path = run_dir / result["artifact"]
        content = artifact_path.read_text(encoding="utf-8")
        content_lower = content.lower()
        assert "do not restart" in content_lower or "restart" in content_lower, (
            "Handoff must mention not restarting"
        )
        auto_exec_phrases = ["start the build", "run the build", "execute the sprint", "launch claude"]
        for phrase in auto_exec_phrases:
            assert phrase not in content_lower, (
                f"Handoff must not contain auto-execution phrase '{phrase}'"
            )


# Test 7: ensure_selected_sprint_scope is idempotent — pre-existing file is not overwritten
def test_ensure_sprint_scope_idempotent():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _raw_idea_run_with_arch(run_dir)
        pre_existing = (
            "# Selected Sprint 1 Scope\n\n"
            "## Sprint Title\nPre-existing Title\n\n"
            "## Sprint Goal\nPre-existing goal text.\n"
        )
        scope_path = run_dir / "selected_feature_sprint_scope.md"
        scope_path.write_text(pre_existing, encoding="utf-8")

        result = so.ensure_selected_sprint_scope(run_dir, 1)
        assert scope_path.read_text(encoding="utf-8") == pre_existing, (
            "ensure_selected_sprint_scope must not overwrite a pre-existing scope file"
        )
        assert result["sprint_title"] == "Pre-existing Title"
        assert result["sprint_goal"] == "Pre-existing goal text."


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            import traceback
            print(f"  FAIL  {t.__name__}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)
