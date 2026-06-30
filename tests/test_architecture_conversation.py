"""
Architecture Conversation — interactive architecture sign-off.

Covers:
 1.  Architecture question template includes frontend/backend/data/auth/build workflow questions.
 2.  Architecture conversation cannot start before requirements approval.
 3.  Architecture conversation initializes after requirements approval.
 4.  Architecture draft is written on init.
 5.  Saving an architecture answer updates state and regenerates architecture_draft.md.
 6.  Required unanswered architecture questions block approval.
 7.  Approval writes approved_architecture.md.
 8.  Approval writes architecture_signoff_state.json with status approved.
 9.  Planning gate detects architecture approval.
10.  Planning gate still blocks build until GLOBAL_INSTRUCTIONS.md exists.
11.  Backend GET endpoint returns can_start=false when requirements missing.
12.  Backend GET endpoint initializes conversation when requirements are approved.
13.  Backend answer endpoint updates state and regenerates draft.
14.  Backend approve endpoint returns updated planning gate.
15.  Older runs do not crash.
16.  Planning gate distinguishes between requirements/architecture/GI blocking.

Fixture runs only — never uses real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import architecture_conversation as ac
import requirements_conversation as rc
import planning_gate as pg
import backend.app as app_mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def write_requirements_signoff(run_dir: Path, status: str = "approved") -> None:
    """Write requirements_signoff_state.json so can_start_architecture returns True."""
    (run_dir / rc.SIGNOFF_ARTIFACT).write_text(
        json.dumps({"status": status})
    )


def write_architecture_signoff(run_dir: Path, status: str = "approved") -> None:
    (run_dir / ac.SIGNOFF_ARTIFACT).write_text(
        json.dumps({"status": status})
    )


def write_global_instructions(run_dir: Path) -> None:
    (run_dir / pg.GLOBAL_INSTRUCTIONS_FILE).write_text("# Global instructions\n")


def answer_all_required(run_dir: Path, conv: dict) -> None:
    """Inject a non-empty answer for every required architecture question."""
    for q in conv.get("questions", []):
        if q.get("required", True):
            ac.save_answer(run_dir, q["id"], q.get("recommended") or "Test answer", "")


def make_run_dir(tmp: Path, **run_state_extra) -> Path:
    run_dir = tmp / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"run_id": "run_001", "status": "done", **run_state_extra}
    (run_dir / "run_state.json").write_text(json.dumps(state))
    return run_dir


# ── 1. Question template covers all required topics ───────────────────────────

def test_architecture_questions_cover_required_topics():
    questions = ac.generate_architecture_questions("raw_idea")
    ids = {q["id"] for q in questions}
    assert "frontend_stack" in ids
    assert "backend_stack" in ids
    assert "data_storage" in ids
    assert "auth_scope" in ids
    assert "external_services" in ids
    assert "ui_style" in ids
    assert "build_workflow" in ids
    assert "first_build_scope" in ids
    assert "deployment_now" in ids
    assert "architecture_notes" in ids


def test_architecture_questions_have_correct_types():
    questions = ac.generate_architecture_questions("raw_idea")
    q_map = {q["id"]: q for q in questions}
    assert q_map["frontend_stack"]["type"] == "single_choice"
    assert q_map["backend_stack"]["type"] == "single_choice"
    assert q_map["data_storage"]["type"] == "single_choice"
    assert q_map["auth_scope"]["type"] == "single_choice"
    assert q_map["external_services"]["type"] == "multi_choice"
    assert q_map["deployment_now"]["type"] == "yes_no"
    assert q_map["architecture_notes"]["type"] == "long_text"
    assert q_map["architecture_notes"]["required"] is False


def test_architecture_questions_recommended_values():
    questions_raw = ac.generate_architecture_questions("raw_idea")
    q_map = {q["id"]: q for q in questions_raw}
    assert q_map["frontend_stack"]["recommended"] == "React + TypeScript"
    assert q_map["data_storage"]["recommended"] == "Mock data"
    assert q_map["auth_scope"]["recommended"] == "No auth in v1"
    assert q_map["build_workflow"]["recommended"] == "Sandbox build"
    assert q_map["deployment_now"]["recommended"] == "No"

    questions_upgrade = ac.generate_architecture_questions("existing_app_upgrade")
    q_upgrade = {q["id"]: q for q in questions_upgrade}
    assert q_upgrade["first_build_scope"]["recommended"] == "Existing-app additive sprint"


def test_architecture_questions_are_universal_across_entry_points():
    """Same question IDs must appear for all supported entry points."""
    expected_ids = {
        "frontend_stack", "backend_stack", "data_storage", "auth_scope",
        "external_services", "ui_style", "build_workflow", "first_build_scope",
        "deployment_now", "architecture_notes",
    }
    for ep in ["raw_idea", "written_requirements", "existing_app_upgrade"]:
        ids = {q["id"] for q in ac.generate_architecture_questions(ep)}
        assert ids == expected_ids, f"Missing questions for {ep}: {expected_ids - ids}"


# ── 2. Cannot start before requirements approval ──────────────────────────────

def test_cannot_start_architecture_without_requirements():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        ok, reason = ac.can_start_architecture(run_dir)
        assert ok is False
        assert reason is not None
        assert "requirements" in reason.lower()


def test_init_returns_none_without_requirements():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        result = ac.init_architecture_conversation(run_dir, "raw_idea")
        assert result is None


# ── 3. Initializes after requirements approval ────────────────────────────────

def test_init_succeeds_after_requirements_approved():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir, "approved")
        state = ac.init_architecture_conversation(run_dir, "raw_idea")
        assert state is not None
        assert state["architecture_status"] == "questions_pending"
        assert state["requirements_approved"] is True
        assert len(state["questions"]) == 10


# ── 4. Architecture draft is written ─────────────────────────────────────────

def test_init_writes_draft_and_questions_json():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        ac.init_architecture_conversation(run_dir, "raw_idea")
        assert (run_dir / ac.DRAFT_ARTIFACT).exists()
        assert (run_dir / ac.QUESTIONS_ARTIFACT).exists()
        draft = (run_dir / ac.DRAFT_ARTIFACT).read_text()
        assert "# Architecture Draft" in draft
        assert "Frontend Architecture" in draft
        assert "Backend Architecture" in draft
        assert "Data Storage" in draft
        assert "Authentication" in draft
        assert "Build Workflow" in draft


def test_draft_contains_approved_requirements_text():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        # Write approved_requirements.md with some content
        (run_dir / rc.APPROVED_ARTIFACT).write_text("# Approved Requirements\nProperty search MVP.")
        ac.init_architecture_conversation(run_dir, "raw_idea")
        draft = (run_dir / ac.DRAFT_ARTIFACT).read_text()
        assert "Property search MVP" in draft


# ── 5. Saving answer updates state and regenerates draft ──────────────────────

def test_save_answer_updates_question_and_answers():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        ac.init_architecture_conversation(run_dir, "raw_idea")
        updated = ac.save_answer(run_dir, "frontend_stack", "React + TypeScript", "Use Vite.")
        assert updated["answers"]["frontend_stack"] == "React + TypeScript"
        q = next(q for q in updated["questions"] if q["id"] == "frontend_stack")
        assert q["answer"] == "React + TypeScript"
        assert q["freeform_answer"] == "Use Vite."


def test_save_answer_regenerates_draft_with_choice():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        ac.init_architecture_conversation(run_dir, "raw_idea")
        ac.save_answer(run_dir, "frontend_stack", "Next.js", "")
        draft = (run_dir / ac.DRAFT_ARTIFACT).read_text()
        assert "Next.js" in draft


def test_save_answer_unknown_question_raises():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        ac.init_architecture_conversation(run_dir, "raw_idea")
        try:
            ac.save_answer(run_dir, "nonexistent_q", "x", "")
            assert False, "Expected ValueError"
        except ValueError:
            pass


# ── 6. Required unanswered questions block approval ───────────────────────────

def test_unanswered_required_questions_block_architecture_approval():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        ac.init_architecture_conversation(run_dir, "raw_idea")
        result = ac.approve_architecture(run_dir)
        assert result["approved"] is False
        assert "required" in (result["error"] or "").lower() or "unanswered" in (result["error"] or "").lower()
        assert not (run_dir / ac.SIGNOFF_ARTIFACT).exists()


# ── 7. Approval writes approved_architecture.md ───────────────────────────────

def test_approve_writes_approved_architecture_md():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        conv = ac.init_architecture_conversation(run_dir, "raw_idea")
        answer_all_required(run_dir, conv)
        result = ac.approve_architecture(run_dir)
        assert result["approved"] is True
        assert (run_dir / ac.APPROVED_ARTIFACT).exists()
        content = (run_dir / ac.APPROVED_ARTIFACT).read_text()
        assert "Approved Architecture" in content
        assert "Architecture Decisions" in content


# ── 8. Approval writes architecture_signoff_state.json ───────────────────────

def test_approve_writes_signoff_with_status_approved():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        conv = ac.init_architecture_conversation(run_dir, "raw_idea")
        answer_all_required(run_dir, conv)
        ac.approve_architecture(run_dir)
        signoff = json.loads((run_dir / ac.SIGNOFF_ARTIFACT).read_text())
        assert signoff["status"] == "approved"
        assert signoff["approved_by"] == "user"
        assert "approved_at" in signoff
        assert signoff["approved_architecture_artifact"] == ac.APPROVED_ARTIFACT


# ── 9. Planning gate detects architecture approval ────────────────────────────

def test_planning_gate_detects_architecture_approval():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        conv = ac.init_architecture_conversation(run_dir, "raw_idea")
        answer_all_required(run_dir, conv)
        ac.approve_architecture(run_dir)

        gate = pg.build_planning_gate_state(
            entry_point="raw_idea",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate["requirements_approved"] is True
        assert gate["architecture_approved"] is True
        # GI not yet created → still blocked
        assert gate["build_allowed_by_planning_gate"] is False
        assert "GLOBAL_INSTRUCTIONS" in gate["planning_gate_reason"] or "global" in gate["planning_gate_reason"].lower()


# ── 10. Planning gate blocks until GLOBAL_INSTRUCTIONS.md exists ─────────────

def test_planning_gate_blocks_until_global_instructions_exist():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # Both req + arch approved, but no GI
        write_requirements_signoff(run_dir)
        write_architecture_signoff(run_dir)
        # No GLOBAL_INSTRUCTIONS.md

        gate = pg.build_planning_gate_state(
            entry_point="existing_app_upgrade",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate["requirements_approved"] is True
        assert gate["architecture_approved"] is True
        assert gate["global_instructions_created"] is False
        assert gate["build_allowed_by_planning_gate"] is False
        assert "GLOBAL_INSTRUCTIONS" in gate["planning_gate_reason"] or "global" in gate["planning_gate_reason"].lower()

        # Now add GI → gate should allow
        write_global_instructions(run_dir)
        gate2 = pg.build_planning_gate_state(
            entry_point="existing_app_upgrade",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate2["build_allowed_by_planning_gate"] is True


# ── 11-12. Backend GET endpoint ────────────────────────────────────────────────

def test_backend_architecture_get_returns_can_start_false_without_requirements(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        (run_dir / "run_state.json").write_text(json.dumps({"run_id": "run_001", "status": "done", "upgrade_mode": True}))

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.get("/api/runs/run_001/architecture-conversation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["can_start"] is False
        assert data["blocking_reason"] is not None
        assert "requirements" in data["blocking_reason"].lower()


def test_backend_architecture_get_initializes_when_requirements_approved(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        (run_dir / "run_state.json").write_text(json.dumps({"run_id": "run_001", "status": "done", "upgrade_mode": True}))
        write_requirements_signoff(run_dir)

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.get("/api/runs/run_001/architecture-conversation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["can_start"] is True
        assert data["conversation"]["architecture_status"] == "questions_pending"
        assert len(data["conversation"]["questions"]) == 10
        assert (run_dir / ac.QUESTIONS_ARTIFACT).exists()


# ── 13. Backend answer endpoint ───────────────────────────────────────────────

def test_backend_architecture_answer_endpoint_saves_answer(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        (run_dir / "run_state.json").write_text(json.dumps({"run_id": "run_001", "status": "done"}))
        write_requirements_signoff(run_dir)
        ac.init_architecture_conversation(run_dir, "raw_idea")

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_001/architecture-conversation/answer",
            json={"question_id": "frontend_stack", "answer": "Next.js", "freeform_answer": "Use App Router."},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["conversation"]["answers"]["frontend_stack"] == "Next.js"
        q = next(q for q in data["conversation"]["questions"] if q["id"] == "frontend_stack")
        assert q["answer"] == "Next.js"


def test_backend_architecture_answer_missing_question_id_returns_400(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        (run_dir / "run_state.json").write_text(json.dumps({"run_id": "run_001", "status": "done"}))
        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_001/architecture-conversation/answer",
            json={"answer": "React + TypeScript"},
            content_type="application/json",
        )
        assert resp.status_code == 400


# ── 14. Backend approve endpoint ──────────────────────────────────────────────

def test_backend_architecture_approve_returns_planning_gate(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        (run_dir / "run_state.json").write_text(json.dumps({"run_id": "run_001", "status": "done", "entry_point": "raw_idea"}))
        write_requirements_signoff(run_dir)
        conv = ac.init_architecture_conversation(run_dir, "raw_idea")
        answer_all_required(run_dir, conv)

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_001/architecture-conversation/approve",
            json={}, content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["approved"] is True
        assert data["conversation"]["architecture_approved"] is True
        assert "planning_gate" in data
        # Architecture approved; but GI still missing → build still blocked
        assert data["planning_gate"]["architecture_approved"] is True
        assert data["planning_gate"]["global_instructions_created"] is False
        assert data["planning_gate"]["build_allowed_by_planning_gate"] is False
        assert "GLOBAL_INSTRUCTIONS" in data["planning_gate"]["planning_gate_reason"] or \
               "global" in data["planning_gate"]["planning_gate_reason"].lower()


def test_backend_architecture_approve_blocks_when_unanswered(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        (run_dir / "run_state.json").write_text(json.dumps({"run_id": "run_001", "status": "done"}))
        write_requirements_signoff(run_dir)
        ac.init_architecture_conversation(run_dir, "raw_idea")
        # No answers saved

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_001/architecture-conversation/approve",
            json={}, content_type="application/json",
        )
        assert resp.status_code == 400


# ── 15. Older runs do not crash ───────────────────────────────────────────────

def test_older_run_without_architecture_artifacts_does_not_crash():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        old_state = {"run_id": "run_000", "status": "done", "mode": "existing_app_upgrade"}
        state = ac.lazy_init_from_run_state(run_dir, old_state)
        assert "architecture_status" in state
        assert "entry_point" in state
        assert "questions" in state


def test_bugfix_run_returns_not_applicable():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        state = ac.lazy_init_from_run_state(run_dir, {"bugfix_mode": True})
        assert state["architecture_status"] == "not_applicable"


def test_init_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        write_requirements_signoff(run_dir)
        ac.init_architecture_conversation(run_dir, "raw_idea")
        # Mark as approved to test idempotence
        q_path = run_dir / ac.QUESTIONS_ARTIFACT
        data = json.loads(q_path.read_text())
        data["architecture_status"] = "approved"
        q_path.write_text(json.dumps(data))
        second = ac.init_architecture_conversation(run_dir, "raw_idea")
        assert second["architecture_status"] == "approved"


# ── 16. Planning gate distinguishes requirements/architecture/GI blocking ──────

def test_planning_gate_reason_when_only_requirements_missing():
    """When only requirements are missing, reason should mention requirements."""
    gate = pg.build_planning_gate_state(
        entry_point="raw_idea",
        execution_mode="build",
        build_requested=True,
        requirements_status="not_started",
        architecture_status="not_started",
        global_instructions_status="not_created",
    )
    assert gate["build_allowed_by_planning_gate"] is False
    # Reason should be about requirements (the first blocker)
    reason = gate["planning_gate_reason"].lower()
    assert "requirements" in reason or "architecture" in reason


def test_planning_gate_reason_when_only_architecture_missing():
    """When requirements approved but only arch missing, reason should mention architecture."""
    gate = pg.build_planning_gate_state(
        entry_point="raw_idea",
        execution_mode="build",
        build_requested=True,
        requirements_status="approved",
        architecture_status="not_started",
        global_instructions_status="created",
    )
    assert gate["build_allowed_by_planning_gate"] is False
    assert "architecture" in gate["planning_gate_reason"].lower()


def test_planning_gate_reason_when_only_global_instructions_missing():
    """When req + arch approved but GI missing, reason should mention GLOBAL_INSTRUCTIONS."""
    gate = pg.build_planning_gate_state(
        entry_point="raw_idea",
        execution_mode="build",
        build_requested=True,
        requirements_status="approved",
        architecture_status="approved",
        global_instructions_status="not_created",
    )
    assert gate["build_allowed_by_planning_gate"] is False
    assert "GLOBAL_INSTRUCTIONS" in gate["planning_gate_reason"] or "global" in gate["planning_gate_reason"].lower()


def test_planning_gate_reason_all_approved_allows_build():
    """When all three are approved, build is allowed."""
    gate = pg.build_planning_gate_state(
        entry_point="raw_idea",
        execution_mode="build",
        build_requested=True,
        requirements_status="approved",
        architecture_status="approved",
        global_instructions_status="created",
    )
    assert gate["build_allowed_by_planning_gate"] is True
    assert "satisfied" in gate["planning_gate_reason"].lower()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
