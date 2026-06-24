"""Deterministic tests for the planning/build/acceptance run-state model."""
import inspect
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


def with_temp_runs(fn):
    def wrapped():
        with tempfile.TemporaryDirectory() as td:
            original = p.RUNS_DIR
            p.RUNS_DIR = Path(td) / "runs"
            p.RUNS_DIR.mkdir()
            try:
                fn()
            finally:
                p.RUNS_DIR = original
    return wrapped


@with_temp_runs
def test_sprint_plan_only_marks_non_planning_steps_not_run():
    run_id = "run_001"
    p.init_run(run_id, "test requirements")
    for key in p.STEP_PHASES["planning"]:
        p._set_step(run_id, key, "complete")
    p.finalize_plan_only_step_state(run_id, selected_sprint=1, sprint_plan_only=True)
    state = p.load_state(run_id)
    for key in p.PLAN_ONLY_NOT_RUN_STEPS:
        assert state["steps"][key]["status"] == "not_run", key
    assert state["steps"]["planning_consistency_check"]["status"] == "complete"
    assert state["steps"]["sprint_report"]["status"] == "complete"


@with_temp_runs
def test_plan_only_has_no_fake_sprint_requirements_check():
    run_id = "run_001"
    p.init_run(run_id, "test requirements")
    p.finalize_plan_only_step_state(run_id, selected_sprint=2, sprint_plan_only=True)
    run = p.run_dir(run_id)
    assert not (run / "sprint_requirements_check.txt").exists()
    report = (run / "sprint_report.md").read_text()
    assert "no sprint was built" in report
    assert "Claude Code was not invoked" in report
    assert "Sprint Requirements Check was not run" in report


def test_planning_consistency_is_pre_build_and_aliases_existing_artifact():
    assert p.STEP_KEYS.index("planning_consistency_check") < p.STEP_KEYS.index("claude_build")
    source = inspect.getsource(p.pipeline)
    consistency_write = source.index('save_artifact(run_id, "requirements_consistency_check.txt"')
    build_call = source.index("mvp_dir = build_mvp(")
    assert consistency_write < build_call


def test_governance_precedes_judgment_and_consolidated_fix():
    assert p.STEP_KEYS.index("governance_review") < p.STEP_KEYS.index("consolidated_fix_plan")
    source = inspect.getsource(p.pipeline)
    governance_call = source.index("gov_verdict, gov_meta_report = _run_governance_review(")
    judgment_call = source.index("judged_verdict, judged_report_text = judge_deepseek_criticism(")
    consolidated_call = source.index("fix_prompt = generate_consolidated_fix_plan(")
    assert governance_call < judgment_call < consolidated_call


def test_consolidated_fix_plan_contains_all_review_sources():
    with tempfile.TemporaryDirectory() as td:
        plan = p.generate_consolidated_fix_plan(
            "spec", Path(td), "SMOKE FAILURE", "DEEPSEEK ISSUE", "JUDGED ISSUE",
            "GOVERNANCE ISSUE", "PLANNING CONSTRAINT", 1,
        )
    for evidence in ("SMOKE FAILURE", "JUDGED ISSUE", "GOVERNANCE ISSUE", "PLANNING CONSTRAINT"):
        assert evidence in plan


@with_temp_runs
def test_real_sprint_plan_only_pipeline_stops_before_claude():
    original_gpt, original_gpt4o, original_build = p.gpt, p.gpt4o, p.build_mvp
    p.gpt = lambda *_args, **_kwargs: "# Generated planning artifact\n"
    p.gpt4o = lambda *_args, **_kwargs: json.dumps({
        "product_name": "Dashboard",
        "complexity_level": "simple",
        "recommended_sprint_count": 2,
        "reason_for_sprint_count": "Two independently demoable dashboard capabilities.",
        "sprints": [
            {"number": 1, "title": "Dashboard Shell", "goal": "Show dashboard shell",
             "requirements_covered": ["Dashboard"], "build_items": ["Dashboard shell"],
             "completion_criteria": ["Shell renders"], "dependencies": []},
            {"number": 2, "title": "Dashboard Cards", "goal": "Show cards",
             "requirements_covered": ["Cards"], "build_items": ["Dashboard cards"],
             "completion_criteria": ["Cards render"], "dependencies": [1]},
        ],
    })
    invoked = []
    p.build_mvp = lambda *_args, **_kwargs: invoked.append(True)
    try:
        run_id = p.pipeline(
            "Build a frontend dashboard with cards.", mode="requirements",
            sprint_plan_only=True, use_deepseek=False,
        )
        state = p.load_state(run_id)
        assert not invoked
        assert state["steps"]["planning_consistency_check"]["status"] == "complete"
        assert state["steps"]["claude_build"]["status"] == "not_run"
        assert state["steps"]["sprint_requirements_check"]["status"] == "not_run"
        assert not (p.run_dir(run_id) / "sprint_requirements_check.txt").exists()
    finally:
        p.gpt, p.gpt4o, p.build_mvp = original_gpt, original_gpt4o, original_build


if __name__ == "__main__":
    test_sprint_plan_only_marks_non_planning_steps_not_run()
    test_plan_only_has_no_fake_sprint_requirements_check()
    test_planning_consistency_is_pre_build_and_aliases_existing_artifact()
    test_governance_precedes_judgment_and_consolidated_fix()
    test_consolidated_fix_plan_contains_all_review_sources()
    test_real_sprint_plan_only_pipeline_stops_before_claude()
    print("PASS: pipeline step-state tests")
