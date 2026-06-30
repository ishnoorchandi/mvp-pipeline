"""
Sprint Orchestrator — prompt generation and completion approval.

Covers:
 1.  Generate build prompt requires initialized orchestrator.
 2.  Build prompt includes GLOBAL_INSTRUCTIONS, requirements, approved_architecture,
     sprint scope, active sprint, current phase, next action.
 3.  Build prompt updates state to build_prompt_ready.
 4.  Generate fix prompt auto-detects smoke failure.
 5.  Fix prompt includes failed smoke summary/artifact.
 6.  Generate fix prompt detects review failure.
 7.  Generate fix prompt detects governance failure.
 8.  Continuation prompt includes current phase and next action.
 9.  Completion cannot be approved without user_approved=True.
10.  Completion cannot be approved if smoke missing.
11.  Completion cannot be approved if review missing.
12.  Completion cannot be approved if governance missing.
13.  Completion can be approved when smoke/review/governance passed.
14.  Completion can be approved when checks waived with waiver reason.
15.  Waived check without waiver reason does not count.
16.  Completion writes sprint_<n>_completion_approval.md.
17.  Backend generate-build-prompt endpoint function works (unit).
18.  Backend generate-fix-prompt endpoint function works (unit).
19.  Backend generate-continuation-prompt endpoint function works (unit).
20.  Backend approve-completion rejects incomplete checks.
21.  Backend approve-completion succeeds when ready.
22.  Build prompt artifact name recorded in state.
23.  Fix prompt detects governance failure.
24.  Continuation prompt is shorter/optimised vs handoff.

Fixture runs only — never uses real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sprint_orchestrator as so


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_full_run(run_dir: Path, build_ready: bool = True) -> None:
    (run_dir / "requirements_signoff_state.json").write_text(
        json.dumps({"status": "approved"}), encoding="utf-8"
    )
    (run_dir / "architecture_signoff_state.json").write_text(
        json.dumps({"status": "approved"}), encoding="utf-8"
    )
    (run_dir / "approved_requirements.md").write_text("# Requirements\n", encoding="utf-8")
    (run_dir / "approved_architecture.md").write_text("# Architecture\n", encoding="utf-8")
    (run_dir / "requirements.md").write_text("# Requirements\n", encoding="utf-8")
    (run_dir / "GLOBAL_INSTRUCTIONS.md").write_text("# GLOBAL_INSTRUCTIONS\n", encoding="utf-8")
    plan = {
        "total_sprints": 1,
        "sprints": [{
            "sprint_number": 1,
            "title": "Dashboard Sprint",
            "goal": "Build the dashboard",
            "features": ["Dashboard view"],
            "likely_files_created": ["src/Dashboard.tsx"],
            "likely_files_modified": ["src/App.tsx"],
            "must_not_modify": [],
            "quality": {"build_ready": build_ready, "risk_level": "medium", "quality_score": 84},
        }],
    }
    (run_dir / "feature_sprint_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (run_dir / "run_state.json").write_text(
        json.dumps({"entry_point": "existing_app_upgrade", "execution_mode": "build"}),
        encoding="utf-8",
    )


def _init_run(run_dir: Path) -> dict:
    _make_full_run(run_dir)
    return so.initialize_orchestrator(run_dir, 1)


def _pass_all_checks(run_dir: Path) -> dict:
    so.record_build_attempt(run_dir, "completed", summary="Build done.")
    so.record_smoke_result(run_dir, "passed", summary="All tests green.")
    so.record_review_result(run_dir, "passed", summary="Review approved.")
    return so.record_governance_result(run_dir, "passed", summary="Governance passed.")


# ── 1. Generate build prompt requires initialized orchestrator ─────────────────

def test_build_prompt_requires_init():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        result = so.generate_sprint_build_prompt(run_dir)
        assert not result["success"]
        assert result["artifact"] is None
        assert "not initialized" in result["error"].lower() or "initialized" in result["error"]


# ── 2. Build prompt includes all required content ─────────────────────────────

def test_build_prompt_content():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        (run_dir / "selected_feature_sprint_scope.md").write_text(
            "# Sprint 1 Scope\nBuild dashboard.\n", encoding="utf-8"
        )
        result = so.generate_sprint_build_prompt(run_dir)
        assert result["success"]
        content = (run_dir / result["artifact"]).read_text()
        assert "GLOBAL_INSTRUCTIONS.md" in content
        assert "requirements.md" in content
        assert "approved_architecture.md" in content
        assert "Sprint 1" in content
        assert "Dashboard Sprint" in content
        assert "Build the dashboard" in content
        assert "build_prompt_ready" in content or "current phase" in content.lower()
        assert "Non-Negotiable Rules" in content or "Non-negotiable" in content.lower()
        assert "Mandatory Reading Order" in content
        assert "Expected Output" in content


# ── 3. Build prompt updates state to build_prompt_ready ──────────────────────

def test_build_prompt_updates_phase():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.generate_sprint_build_prompt(run_dir)
        state = so.load_orchestrator_state(run_dir)
        assert state["current_phase"] == so.PHASE_BUILD_PROMPT_READY
        assert state["build_prompt_artifact"] == "sprint_1_build_prompt.md"
        assert state["next_action"]  # next_action is set after build prompt generation


# ── 4. Fix prompt auto-detects smoke failure ──────────────────────────────────

def test_fix_prompt_detects_smoke_failure():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "failed", summary="Tests timed out.", artifact="smoke_log.txt")
        result = so.generate_sprint_fix_prompt(run_dir)
        assert result["success"]
        content = (run_dir / result["artifact"]).read_text()
        assert "smoke" in content.lower() or "Smoke" in content
        assert "Tests timed out" in content


# ── 5. Fix prompt includes failed smoke summary/artifact ─────────────────────

def test_fix_prompt_includes_smoke_details():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "failed", summary="npm test failed", artifact="smoke_mutation_report.md")
        result = so.generate_sprint_fix_prompt(run_dir)
        content = (run_dir / result["artifact"]).read_text()
        assert "npm test failed" in content
        assert "smoke_mutation_report.md" in content


# ── 6. Fix prompt detects review failure ─────────────────────────────────────

def test_fix_prompt_detects_review_failure():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "passed")
        so.record_review_result(run_dir, "failed", summary="Review found regressions.")
        result = so.generate_sprint_fix_prompt(run_dir)
        content = (run_dir / result["artifact"]).read_text()
        assert "review" in content.lower()
        assert "Review found regressions" in content


# ── 7. Fix prompt detects governance failure ──────────────────────────────────

def test_fix_prompt_detects_governance_failure():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "passed")
        so.record_review_result(run_dir, "passed")
        so.record_governance_result(run_dir, "failed", summary="Change boundary violated.")
        result = so.generate_sprint_fix_prompt(run_dir)
        content = (run_dir / result["artifact"]).read_text()
        assert "governance" in content.lower() or "Governance" in content
        assert "Change boundary violated" in content


# ── 8. Continuation prompt includes phase and next action ────────────────────

def test_continuation_prompt_content():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "failed", summary="Tests failed.")
        result = so.generate_sprint_continuation_prompt(run_dir)
        assert result["success"]
        content = (run_dir / result["artifact"]).read_text()
        assert "smoke_failed" in content or "smoke" in content.lower()
        assert "Next action" in content or "next action" in content.lower()
        assert "GLOBAL_INSTRUCTIONS.md" in content
        assert "Do not restart from scratch" in content


# ── 9. Completion requires user_approved=True ─────────────────────────────────

def test_completion_requires_user_approved():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        _pass_all_checks(run_dir)
        result = so.approve_sprint_completion(run_dir, user_approved=False)
        assert not result["success"]
        assert "approval" in result["error"].lower() or "user_approved" in result["error"]


# ── 10. Completion rejected if smoke missing ──────────────────────────────────

def test_completion_rejected_smoke_missing():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        # Only build attempt, no checks
        so.record_build_attempt(run_dir, "completed")
        result = so.approve_sprint_completion(run_dir, user_approved=True)
        assert not result["success"]
        assert "smoke" in result["error"].lower()


# ── 11. Completion rejected if review missing ─────────────────────────────────

def test_completion_rejected_review_missing():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "passed")
        # No review
        result = so.approve_sprint_completion(run_dir, user_approved=True)
        assert not result["success"]
        assert "review" in result["error"].lower()


# ── 12. Completion rejected if governance missing ─────────────────────────────

def test_completion_rejected_governance_missing():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "passed")
        so.record_review_result(run_dir, "passed")
        # No governance
        result = so.approve_sprint_completion(run_dir, user_approved=True)
        assert not result["success"]
        assert "governance" in result["error"].lower()


# ── 13. Completion succeeds when all checks passed ────────────────────────────

def test_completion_succeeds_all_passed():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        _pass_all_checks(run_dir)
        result = so.approve_sprint_completion(run_dir, user_approved=True, approval_note="All done.")
        assert result["success"], result.get("error")
        assert result["state"]["status"] == so.STATUS_COMPLETED
        assert result["state"]["current_phase"] == so.PHASE_COMPLETED


# ── 14. Completion succeeds when checks waived with reason ───────────────────

def test_completion_succeeds_waived_checks():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "waived", waived=True, waiver_reason="No test runner available.")
        so.record_review_result(run_dir, "passed")
        so.record_governance_result(run_dir, "waived", waived=True, waiver_reason="Governance waived by user.")
        result = so.approve_sprint_completion(run_dir, user_approved=True)
        assert result["success"], result.get("error")
        assert result["state"]["status"] == so.STATUS_COMPLETED


# ── 15. Waived check without waiver_reason does not count ────────────────────

def test_waived_without_reason_not_accepted():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "waived", waived=True, waiver_reason="")  # empty reason
        so.record_review_result(run_dir, "passed")
        so.record_governance_result(run_dir, "passed")
        ok, reason = so.can_complete_sprint(so.load_orchestrator_state(run_dir))
        assert not ok
        assert "smoke" in reason.lower()


# ── 16. Completion writes sprint_<n>_completion_approval.md ──────────────────

def test_completion_writes_approval_artifact():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        _pass_all_checks(run_dir)
        result = so.approve_sprint_completion(run_dir, user_approved=True, approval_note="Ship it.")
        assert (run_dir / "sprint_1_completion_approval.md").exists()
        content = (run_dir / "sprint_1_completion_approval.md").read_text()
        assert "Completed" in content
        assert "Ship it" in content
        assert "PASSED" in content or "Passed" in content.lower() or "passed" in content


# ── 17. generate_sprint_build_prompt works (unit) ────────────────────────────

def test_build_prompt_unit():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        result = so.generate_sprint_build_prompt(run_dir)
        assert result["success"]
        assert result["artifact"] == "sprint_1_build_prompt.md"
        assert (run_dir / "sprint_1_build_prompt.md").exists()


# ── 18. generate_sprint_fix_prompt works (unit) ──────────────────────────────

def test_fix_prompt_unit_explicit_type():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        result = so.generate_sprint_fix_prompt(run_dir, failure_type="smoke")
        assert result["success"]
        assert result["artifact"] == "sprint_1_fix_prompt.md"
        assert (run_dir / "sprint_1_fix_prompt.md").exists()


# ── 19. generate_sprint_continuation_prompt works (unit) ─────────────────────

def test_continuation_prompt_unit():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        result = so.generate_sprint_continuation_prompt(run_dir)
        assert result["success"]
        assert result["artifact"] == "sprint_1_continuation_prompt.md"
        assert (run_dir / "sprint_1_continuation_prompt.md").exists()
        state = so.load_orchestrator_state(run_dir)
        assert state["continuation_prompt_artifact"] == "sprint_1_continuation_prompt.md"


# ── 20. approve-completion rejects incomplete checks (unit) ──────────────────

def test_approve_completion_rejects_incomplete():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        result = so.approve_sprint_completion(run_dir, user_approved=True)
        assert not result["success"]
        assert "smoke" in result["error"].lower()


# ── 21. approve-completion succeeds when ready (unit) ────────────────────────

def test_approve_completion_succeeds_ready():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        _pass_all_checks(run_dir)
        result = so.approve_sprint_completion(run_dir, user_approved=True)
        assert result["success"]
        state = so.load_orchestrator_state(run_dir)
        assert state["status"] == so.STATUS_COMPLETED
        assert state.get("completion_approval_artifact") == "sprint_1_completion_approval.md"


# ── 22. Build prompt artifact name recorded in state ─────────────────────────

def test_build_prompt_artifact_in_state():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.generate_sprint_build_prompt(run_dir)
        state = so.load_orchestrator_state(run_dir)
        assert state.get("build_prompt_artifact") == "sprint_1_build_prompt.md"


# ── 23. Fix prompt with explicit governance type ──────────────────────────────

def test_fix_prompt_explicit_governance():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        result = so.generate_sprint_fix_prompt(run_dir, failure_type="governance")
        assert result["success"]
        content = (run_dir / result["artifact"]).read_text()
        assert "governance" in content.lower() or "Governance" in content


# ── 24. Continuation prompt is shorter than handoff ──────────────────────────

def test_continuation_shorter_than_handoff():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_run(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "failed")
        handoff_result = so.generate_handoff(run_dir)
        cont_result = so.generate_sprint_continuation_prompt(run_dir)
        handoff_len = len((run_dir / handoff_result["artifact"]).read_text())
        cont_len = len((run_dir / cont_result["artifact"]).read_text())
        assert cont_len < handoff_len, f"Continuation ({cont_len}) should be shorter than handoff ({handoff_len})"


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
