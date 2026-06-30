"""
Demo safety audit tests.

Verifies that:
1.  Build prompt includes copy/manual safety language.
2.  Build prompt does not include wording that implies automatic execution.
3.  Fix prompt includes copy/manual safety language.
4.  Continuation prompt includes copy/manual safety language.
5.  Handoff prompt says "Do not restart from scratch".
6.  Build prompt includes "Non-Negotiable Rules" section.
7.  Global instructions include Sprint Orchestrator Expectations section.
8.  Backend sprint orchestrator GET returns safe defaults for uninitialized run.
9.  Sprint orchestrator GET returns can_initialize=False before approvals.
10. Planning gate blocking reason priority: requirements → architecture → global instructions.
11. Old-run operator summary does not crash with missing planning/orchestrator fields.
12. Waived checks without a waiver_reason do not satisfy the completion gate.
13. Sprint completion gate requires all three checks.
14. Older run operator summary is safe with minimal run_state.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sprint_orchestrator as so
import planning_gate as pg


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _full_run(run_dir: Path) -> None:
    (run_dir / "requirements_signoff_state.json").write_text(
        json.dumps({"status": "approved"}), encoding="utf-8"
    )
    (run_dir / "architecture_signoff_state.json").write_text(
        json.dumps({"status": "approved"}), encoding="utf-8"
    )
    (run_dir / "approved_requirements.md").write_text("# Requirements\n", encoding="utf-8")
    (run_dir / "approved_architecture.md").write_text("# Architecture\n", encoding="utf-8")
    (run_dir / "requirements.md").write_text("# Requirements\n", encoding="utf-8")
    (run_dir / "GLOBAL_INSTRUCTIONS.md").write_text(
        "# GLOBAL_INSTRUCTIONS\n## Sprint Orchestrator Expectations\nSee below.\n",
        encoding="utf-8",
    )
    plan = {
        "total_sprints": 1,
        "sprints": [{
            "sprint_number": 1,
            "title": "Feature Sprint",
            "goal": "Build the feature",
            "features": ["Feature A"],
            "likely_files_created": [],
            "likely_files_modified": [],
            "must_not_modify": [],
            "quality": {"build_ready": True, "risk_level": "low", "quality_score": 90},
        }],
    }
    (run_dir / "feature_sprint_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (run_dir / "run_state.json").write_text(
        json.dumps({"entry_point": "existing_app_upgrade", "execution_mode": "build"}),
        encoding="utf-8",
    )


def _init_orch(run_dir: Path) -> None:
    _full_run(run_dir)
    so.initialize_orchestrator(run_dir, 1)


def _all_checks_passed(run_dir: Path) -> None:
    so.record_build_attempt(run_dir, "completed", summary="Build done.")
    so.record_smoke_result(run_dir, "passed", summary="Green.")
    so.record_review_result(run_dir, "passed", summary="Reviewed.")
    so.record_governance_result(run_dir, "passed", summary="Approved.")


# ── 1. Build prompt includes copy/manual safety language ──────────────────────

def test_build_prompt_has_copy_safety_language():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_orch(run_dir)
        r = so.generate_sprint_build_prompt(run_dir)
        assert r["success"]
        content = (run_dir / r["artifact"]).read_text()
        assert "do not run Claude Code automatically" in content.lower() or \
               "does not run claude code automatically" in content.lower() or \
               "These prompts do not run Claude Code automatically" in content


# ── 2. Build prompt does not imply automatic execution ────────────────────────

def test_build_prompt_no_automatic_execution_language():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_orch(run_dir)
        r = so.generate_sprint_build_prompt(run_dir)
        content = (run_dir / r["artifact"]).read_text().lower()
        for bad_phrase in ("run build automatically", "start build automatically",
                           "execute sprint automatically", "launch claude automatically"):
            assert bad_phrase not in content, f"Found disallowed phrase: {bad_phrase!r}"


# ── 3. Fix prompt includes copy/manual safety language ───────────────────────

def test_fix_prompt_has_copy_safety_language():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_orch(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "failed", summary="Tests failed.")
        r = so.generate_sprint_fix_prompt(run_dir)
        assert r["success"]
        content = (run_dir / r["artifact"]).read_text()
        assert "do not run Claude Code automatically" in content.lower() or \
               "These prompts do not run Claude Code automatically" in content


# ── 4. Continuation prompt includes copy/manual safety language ───────────────

def test_continuation_prompt_has_copy_safety_language():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_orch(run_dir)
        r = so.generate_sprint_continuation_prompt(run_dir)
        assert r["success"]
        content = (run_dir / r["artifact"]).read_text()
        assert "do not run Claude Code automatically" in content.lower() or \
               "These prompts do not run Claude Code automatically" in content


# ── 5. Handoff prompt says "Do not restart from scratch" ─────────────────────

def test_handoff_has_no_restart_language():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_orch(run_dir)
        so.record_build_attempt(run_dir, "completed")
        r = so.generate_handoff(run_dir)
        assert r["success"]
        content = (run_dir / r["artifact"]).read_text()
        assert "Do not restart" in content or "do not restart" in content.lower()


# ── 6. Build prompt has Non-Negotiable Rules section ─────────────────────────

def test_build_prompt_has_non_negotiable_rules():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_orch(run_dir)
        r = so.generate_sprint_build_prompt(run_dir)
        content = (run_dir / r["artifact"]).read_text()
        assert "Non-Negotiable Rules" in content or "non-negotiable" in content.lower()


# ── 7. Global instructions include Sprint Orchestrator Expectations ───────────

def test_global_instructions_include_orchestrator_section():
    import global_instructions as gi
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        (run_dir / "requirements_signoff_state.json").write_text(
            json.dumps({"status": "approved"}), encoding="utf-8"
        )
        (run_dir / "architecture_signoff_state.json").write_text(
            json.dumps({"status": "approved"}), encoding="utf-8"
        )
        (run_dir / "approved_requirements.md").write_text("# Requirements\n", encoding="utf-8")
        (run_dir / "approved_architecture.md").write_text("# Architecture\n", encoding="utf-8")
        result = gi.generate_global_instructions(run_dir)
        assert result["success"]
        content = (run_dir / "GLOBAL_INSTRUCTIONS.md").read_text()
        assert "Sprint Orchestrator" in content


# ── 8. Sprint orchestrator GET returns safe defaults for uninitialized run ────

def test_orchestrator_get_safe_defaults_no_state():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        state = so.load_orchestrator_state(run_dir)
        assert state is None
        can_init, reason = so.can_initialize_orchestration(run_dir)
        assert not can_init
        assert reason is not None and len(reason) > 0


# ── 9. Sprint orchestrator GET: can_initialize=False before approvals ─────────

def test_orchestrator_cannot_init_without_approvals():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # No signoff files
        can_init, reason = so.can_initialize_orchestration(run_dir)
        assert not can_init
        assert "requirements" in reason.lower() or "approval" in reason.lower() or reason


# ── 10. Planning gate blocking reason priority ────────────────────────────────

def test_planning_gate_reason_priority_req_first():
    # No approvals at all → requirements mentioned first
    gate = pg.build_planning_gate_state(
        entry_point="existing_app_upgrade",
        execution_mode="build",
        build_requested=True,
        requirements_status="not_started",
        architecture_status="not_started",
        global_instructions_status="not_created",
    )
    assert not gate["build_allowed_by_planning_gate"]
    assert "requirements" in gate["planning_gate_reason"].lower()


def test_planning_gate_reason_priority_arch_when_req_done():
    gate = pg.build_planning_gate_state(
        entry_point="existing_app_upgrade",
        execution_mode="build",
        build_requested=True,
        requirements_status="approved",
        architecture_status="not_started",
        global_instructions_status="not_created",
    )
    assert not gate["build_allowed_by_planning_gate"]
    assert "architecture" in gate["planning_gate_reason"].lower()


def test_planning_gate_reason_priority_gi_when_arch_done():
    gate = pg.build_planning_gate_state(
        entry_point="existing_app_upgrade",
        execution_mode="build",
        build_requested=True,
        requirements_status="approved",
        architecture_status="approved",
        global_instructions_status="not_created",
    )
    assert not gate["build_allowed_by_planning_gate"]
    assert "global_instructions" in gate["planning_gate_reason"].lower() or \
           "GLOBAL_INSTRUCTIONS" in gate["planning_gate_reason"]


# ── 11. Old-run operator summary safe with minimal run_state ─────────────────

def test_old_run_operator_summary_safe_with_empty_state():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app import build_operator_run_summary
    with tempfile.TemporaryDirectory() as td:
        summary = build_operator_run_summary(Path(td), {}, [])
    assert summary is not None
    assert "current_status" in summary or "build_status" in summary
    assert "planning_gate" in summary


def test_old_run_operator_summary_safe_with_no_planning_fields():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app import build_operator_run_summary
    with tempfile.TemporaryDirectory() as td:
        minimal = {"entry_point": "existing_app_upgrade", "execution_mode": "plan_only"}
        summary = build_operator_run_summary(Path(td), minimal, [])
    assert summary is not None
    assert summary.get("planning_gate") is not None


# ── 12. Waived checks without waiver_reason do not satisfy gate ───────────────

def test_waived_without_reason_blocks_completion():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_orch(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "waived", waived=True, waiver_reason="")
        so.record_review_result(run_dir, "passed")
        so.record_governance_result(run_dir, "passed")
        state = so.load_orchestrator_state(run_dir)
        ok, reason = so.can_complete_sprint(state)
        assert not ok
        assert "smoke" in reason.lower()


# ── 13. Sprint completion gate requires all three checks ─────────────────────

def test_completion_requires_smoke_review_governance():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _init_orch(run_dir)
        so.record_build_attempt(run_dir, "completed")
        so.record_smoke_result(run_dir, "passed")
        # missing review and governance
        state = so.load_orchestrator_state(run_dir)
        ok, reason = so.can_complete_sprint(state)
        assert not ok
        assert "review" in reason.lower() or "governance" in reason.lower()


# ── 14. Old-run operator summary handles missing orchestrator fields ──────────

def test_old_run_operator_summary_no_orchestrator():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app import build_operator_run_summary
    with tempfile.TemporaryDirectory() as td:
        old_state = {
            "entry_point": "existing_app_upgrade",
            "execution_mode": "build",
            "status": "feature_plan_only_done",
        }
        summary = build_operator_run_summary(Path(td), old_state, [])
    assert summary is not None
    assert "planning_gate" in summary


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
