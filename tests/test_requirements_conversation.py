"""
Requirements Conversation — interactive requirements sign-off.

Covers:
 1.  Raw idea draft generation produces the expected sections.
 2.  Existing app upgrade draft generation produces the expected sections.
 3.  Written requirements draft generation produces the expected sections.
 4.  Raw idea question template includes MVP/user/data/out-of-scope questions.
 5.  Existing app upgrade question template includes preserve/reuse/additive/backend-scope questions.
 6.  Written requirements question template includes gap-filling questions.
 7.  Requirements conversation state writes requirements_questions.json on init.
 8.  Saving an answer updates the question and answers dict.
 9.  Approval writes approved_requirements.md.
10.  Approval writes requirements_signoff_state.json with status approved.
11.  Planning gate detects approved requirements after approval.
12.  Required unanswered questions prevent approval.
13.  Backend GET endpoint initializes missing conversation lazily.
14.  Backend answer endpoint updates state.
15.  Backend approve endpoint returns updated planning gate.
16.  Older runs without requirements conversation do not crash.
17.  Frontend build succeeds (validated externally via npm run build).

Fixture runs only — never uses real OneHR repos.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requirements_conversation as rc
import planning_gate as pg
import backend.app as app_mod


@pytest.fixture(autouse=True)
def _block_live_llm_calls(monkeypatch):
    """Safety net for the whole file: no test here may reach a live LLM API.

    By default the AI interviewer's single LLM call point raises, so every
    call to generate_requirements_questions()/init_requirements_conversation()
    transparently falls back to template questions unless a test overrides
    this monkeypatch with its own mocked response (see the AI interviewer
    tests below).
    """
    def _blocked(messages):
        raise RuntimeError("Live LLM calls are blocked in tests")
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", _blocked)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_run_dir(tmp: Path, **run_state_extra) -> Path:
    run_dir = tmp / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"run_id": "run_001", "status": "done", **run_state_extra}
    (run_dir / "run_state.json").write_text(json.dumps(state))
    return run_dir


def answer_all_required(run_dir: Path, conv: dict) -> None:
    """Inject a non-empty answer for every required question."""
    for q in conv.get("questions", []):
        if q.get("required", True):
            rc.save_answer(run_dir, q["id"], "Test answer", "")


# ── 1. Raw idea draft generation ──────────────────────────────────────────────

def test_raw_idea_draft_contains_expected_sections():
    draft = rc.generate_requirements_draft("raw_idea", {"raw_input": "A property listing app"})
    assert "# MVP Requirements Draft" in draft
    assert "Target Users" in draft
    assert "Core User Workflows" in draft
    assert "Must-Have Features" in draft
    assert "Nice-to-Have Later" in draft
    assert "Out of Scope" in draft
    assert "Data and External Services" in draft
    assert "Acceptance Criteria" in draft
    # Input text is reflected in the draft
    assert "property listing app" in draft


# ── 2. Existing app upgrade draft generation ──────────────────────────────────

def test_existing_app_upgrade_draft_contains_expected_sections():
    draft = rc.generate_requirements_draft("existing_app_upgrade", {
        "existing_app_path": "/projects/my-app",
        "feature_request": "Add saved filters to the resource browser",
    })
    assert "# Upgrade Requirements Draft" in draft
    assert "Existing App Context" in draft
    assert "Feature Request" in draft
    assert "Preserve / Reuse" in draft
    assert "Additive Changes" in draft
    assert "Out of Scope" in draft
    assert "Risk Notes" in draft
    assert "Acceptance Criteria" in draft
    assert "my-app" in draft
    assert "saved filters" in draft


# ── 3. Written requirements draft generation ──────────────────────────────────

def test_written_requirements_draft_contains_expected_sections():
    draft = rc.generate_requirements_draft("written_requirements", {
        "requirements_text": "The system must allow users to search for properties.",
    })
    assert "# Normalized Requirements Draft" in draft
    assert "Functional Requirements" in draft
    assert "Missing Details" in draft
    assert "External Dependencies" in draft
    assert "Acceptance Criteria" in draft
    assert "search for properties" in draft


# ── 4. Raw idea question template ─────────────────────────────────────────────

def test_raw_idea_questions_cover_required_topics():
    questions = rc.generate_requirements_questions("raw_idea")
    ids = {q["id"] for q in questions}
    # Primary user, core workflow, data source, must-haves, out-of-scope
    assert "primary_user" in ids
    assert "core_workflow" in ids
    assert "data_source" in ids
    assert "must_have_features" in ids
    assert "out_of_scope" in ids
    # data_source should be single_choice with options
    data_q = next(q for q in questions if q["id"] == "data_source")
    assert data_q["type"] == "single_choice"
    assert len(data_q["options"]) >= 3
    # user_accounts should be yes_no
    accounts_q = next(q for q in questions if q["id"] == "user_accounts")
    assert accounts_q["type"] == "yes_no"


# ── 5. Existing app upgrade question template ─────────────────────────────────

def test_existing_app_upgrade_questions_cover_required_topics():
    questions = rc.generate_requirements_questions("existing_app_upgrade")
    ids = {q["id"] for q in questions}
    assert "preserve_pages" in ids
    assert "additive_only" in ids
    assert "backend_db_allowed" in ids
    assert "frontend_first" in ids
    assert "out_of_scope" in ids
    # additive_only should be yes_no
    additive_q = next(q for q in questions if q["id"] == "additive_only")
    assert additive_q["type"] == "yes_no"
    assert additive_q["recommended"] == "Yes"


# ── 6. Written requirements question template ─────────────────────────────────

def test_written_requirements_questions_cover_gap_filling_topics():
    questions = rc.generate_requirements_questions("written_requirements")
    ids = {q["id"] for q in questions}
    assert "must_have_v1" in ids
    assert "auth_included" in ids
    assert "non_goals" in ids
    auth_q = next(q for q in questions if q["id"] == "auth_included")
    assert auth_q["type"] == "yes_no"
    assert auth_q["recommended"] == "No"


# ── 7. Conversation state writes requirements_questions.json ──────────────────

def test_init_writes_questions_json_and_draft():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        state = rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test app"})
        questions_file = run_dir / rc.QUESTIONS_ARTIFACT
        draft_file = run_dir / rc.DRAFT_ARTIFACT
        assert questions_file.exists(), "requirements_questions.json must be written"
        assert draft_file.exists(), "mvp_requirements_draft.md must be written"
        saved = json.loads(questions_file.read_text())
        assert saved["entry_point"] == "raw_idea"
        assert saved["requirements_status"] == "questions_pending"
        assert len(saved["questions"]) > 0
        # Returned state should match written state
        assert state["requirements_status"] == "questions_pending"


def test_init_is_idempotent():
    """Second init call returns existing state without overwriting."""
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test app"})
        # Manually mark as approved to test idempotence
        questions_path = run_dir / rc.QUESTIONS_ARTIFACT
        data = json.loads(questions_path.read_text())
        data["requirements_status"] = "approved"
        questions_path.write_text(json.dumps(data))

        second = rc.init_requirements_conversation(run_dir, "raw_idea")
        assert second["requirements_status"] == "approved", "second init must return existing state"


# ── 8. Saving an answer updates question state ────────────────────────────────

def test_save_answer_updates_question_and_answers_dict():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test app"})
        updated = rc.save_answer(run_dir, "primary_user", "Property managers", "")
        # answers dict should reflect the new value
        assert updated["answers"]["primary_user"] == "Property managers"
        # question list should also be updated
        q = next(q for q in updated["questions"] if q["id"] == "primary_user")
        assert q["answer"] == "Property managers"
        # Verify persistence
        reloaded = rc.load_requirements_conversation(run_dir)
        assert reloaded["answers"]["primary_user"] == "Property managers"


def test_save_answer_freeform_stored():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        rc.init_requirements_conversation(run_dir, "existing_app_upgrade", {"existing_app_path": "/tmp/app", "feature_request": "add filters"})
        updated = rc.save_answer(run_dir, "additive_only", "Yes", "Keep all existing routes exactly.")
        q = next(q for q in updated["questions"] if q["id"] == "additive_only")
        assert q["freeform_answer"] == "Keep all existing routes exactly."


def test_save_answer_unknown_question_raises():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test"})
        try:
            rc.save_answer(run_dir, "nonexistent_question_id", "Anything", "")
            assert False, "Expected ValueError"
        except ValueError:
            pass


# ── 9. Approval writes approved_requirements.md ───────────────────────────────

def test_approve_writes_approved_requirements_md():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        conv = rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test app"})
        answer_all_required(run_dir, conv)
        result = rc.approve_requirements(run_dir)
        assert result["approved"] is True
        approved_file = run_dir / rc.APPROVED_ARTIFACT
        assert approved_file.exists(), "approved_requirements.md must be written"
        content = approved_file.read_text()
        assert "Approved Requirements" in content


# ── 10. Approval writes requirements_signoff_state.json ──────────────────────

def test_approve_writes_signoff_with_status_approved():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        conv = rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test"})
        answer_all_required(run_dir, conv)
        rc.approve_requirements(run_dir)
        signoff_file = run_dir / rc.SIGNOFF_ARTIFACT
        assert signoff_file.exists(), "requirements_signoff_state.json must be written"
        signoff = json.loads(signoff_file.read_text())
        assert signoff["status"] == "approved"
        assert signoff["approved_by"] == "user"
        assert "approved_at" in signoff


# ── 11. Planning gate detects approved requirements ───────────────────────────

def test_planning_gate_detects_approved_requirements_after_approve():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        conv = rc.init_requirements_conversation(run_dir, "existing_app_upgrade", {
            "existing_app_path": "/tmp/app", "feature_request": "add filters",
        })
        answer_all_required(run_dir, conv)
        rc.approve_requirements(run_dir)

        gate = pg.build_planning_gate_state(
            entry_point="existing_app_upgrade",
            execution_mode="build",
            build_requested=True,
            run_dir=run_dir,
        )
        assert gate["requirements_approved"] is True
        assert gate["requirements_status"] == "approved"
        # Architecture is still not approved, so build remains blocked
        assert gate["build_allowed_by_planning_gate"] is False
        assert "architecture" in gate["planning_gate_reason"].lower()


# ── 12. Required unanswered questions prevent approval ────────────────────────

def test_unanswered_required_questions_prevent_approval():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test"})
        # Do NOT answer any questions
        result = rc.approve_requirements(run_dir)
        assert result["approved"] is False
        assert result["error"] is not None
        assert "unanswered" in result["error"].lower() or "required" in result["error"].lower()
        # Signoff file must NOT exist
        assert not (run_dir / rc.SIGNOFF_ARTIFACT).exists()


# ── 13. Backend GET endpoint lazy-inits conversation ─────────────────────────

def test_backend_get_endpoint_lazy_init(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        run_state = {
            "run_id": "run_001", "status": "done",
            "upgrade_mode": True, "mode": "existing_app_upgrade",
        }
        (run_dir / "run_state.json").write_text(json.dumps(run_state))

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.get("/api/runs/run_001/requirements-conversation")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data}"
        data = resp.get_json()
        assert "conversation" in data
        assert data["conversation"]["entry_point"] == "existing_app_upgrade"
        assert data["conversation"]["requirements_status"] == "questions_pending"
        assert len(data["conversation"]["questions"]) > 0
        # Questions file should have been created
        assert (run_dir / rc.QUESTIONS_ARTIFACT).exists()


def test_backend_get_unknown_run_returns_404(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.get("/api/runs/run_nonexistent/requirements-conversation")
        assert resp.status_code == 404


# ── 14. Backend answer endpoint updates state ─────────────────────────────────

def test_backend_answer_endpoint_saves_question(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        run_state = {"run_id": "run_001", "status": "done", "upgrade_mode": True}
        (run_dir / "run_state.json").write_text(json.dumps(run_state))
        # Pre-init conversation
        rc.init_requirements_conversation(run_dir, "existing_app_upgrade", {
            "existing_app_path": "/tmp/app", "feature_request": "filters",
        })

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_001/requirements-conversation/answer",
            json={"question_id": "additive_only", "answer": "Yes", "freeform_answer": "No existing routes changed."},
            content_type="application/json",
        )
        assert resp.status_code == 200, f"Expected 200: {resp.data}"
        data = resp.get_json()
        assert data["conversation"]["answers"]["additive_only"] == "Yes"
        q = next(q for q in data["conversation"]["questions"] if q["id"] == "additive_only")
        assert q["answer"] == "Yes"
        assert q["freeform_answer"] == "No existing routes changed."


def test_backend_answer_missing_question_id_returns_400(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        (run_dir / "run_state.json").write_text(json.dumps({"run_id": "run_001", "status": "done"}))
        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_001/requirements-conversation/answer",
            json={"answer": "Yes"},
            content_type="application/json",
        )
        assert resp.status_code == 400


# ── 15. Backend approve endpoint returns updated planning gate ────────────────

def test_backend_approve_endpoint_returns_planning_gate(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        run_state = {
            "run_id": "run_001", "status": "done",
            "upgrade_mode": True, "entry_point": "existing_app_upgrade",
        }
        (run_dir / "run_state.json").write_text(json.dumps(run_state))
        # Init and answer all required questions
        conv = rc.init_requirements_conversation(run_dir, "existing_app_upgrade", {
            "existing_app_path": "/tmp/app", "feature_request": "filters",
        })
        answer_all_required(run_dir, conv)

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_001/requirements-conversation/approve",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 200, f"Expected 200: {resp.data}"
        data = resp.get_json()
        assert data["approved"] is True
        assert data["conversation"]["requirements_approved"] is True
        assert "planning_gate" in data
        # Requirements are now approved; architecture still not → gate still blocks
        assert data["planning_gate"]["requirements_approved"] is True
        assert data["planning_gate"]["architecture_approved"] is False
        assert data["planning_gate"]["build_allowed_by_planning_gate"] is False


def test_backend_approve_endpoint_blocks_when_unanswered(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        (run_dir / "run_state.json").write_text(json.dumps({"run_id": "run_001", "status": "done", "upgrade_mode": True}))
        rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test"})
        # Do NOT answer any questions

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_001/requirements-conversation/approve",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400


# ── 16. Older runs without requirements conversation do not crash ──────────────

def test_older_run_without_conversation_artifacts_does_not_crash():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # Old run: just a run_state.json, no requirements_questions.json
        old_state = {"run_id": "run_000", "status": "done", "mode": "existing_app_upgrade"}
        state = rc.lazy_init_from_run_state(run_dir, old_state)
        assert "requirements_status" in state
        assert "entry_point" in state
        assert "questions" in state


def test_read_only_run_returns_not_applicable_status():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # Bugfix mode — should return not_applicable
        old_state = {"run_id": "run_000", "status": "done", "bugfix_mode": True}
        state = rc.lazy_init_from_run_state(run_dir, old_state)
        assert state["requirements_status"] == "not_applicable"


def test_get_unanswered_required_returns_empty_when_all_answered():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        conv = rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test"})
        answer_all_required(run_dir, conv)
        # Reload from disk to verify persistence
        reloaded = rc.load_requirements_conversation(run_dir)
        unanswered = rc.get_unanswered_required(reloaded)
        assert unanswered == []


# ── entry_point persistence (bug fix coverage) ────────────────────────────────

def test_lazy_init_persists_entry_point_for_idea_run():
    """lazy_init_from_run_state must write entry_point=raw_idea into run_state.json
    when input_mode='idea' so planning_gate sees the correct value without a reload."""
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_103"
        run_dir.mkdir()
        run_state = {"run_id": "run_103", "status": "plan_only_done", "input_mode": "idea"}
        (run_dir / "run_state.json").write_text(json.dumps(run_state))

        conv = rc.lazy_init_from_run_state(run_dir, run_state)
        assert conv["entry_point"] == "raw_idea"

        # run_state.json on disk must now have entry_point
        persisted = json.loads((run_dir / "run_state.json").read_text())
        assert persisted.get("entry_point") == "raw_idea", (
            f"expected entry_point=raw_idea in run_state.json, got: {persisted}"
        )


def test_lazy_init_persists_entry_point_for_requirements_run():
    """input_mode='requirements' → writes written_requirements into run_state.json."""
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_104"
        run_dir.mkdir()
        run_state = {"run_id": "run_104", "status": "plan_only_done", "input_mode": "requirements"}
        (run_dir / "run_state.json").write_text(json.dumps(run_state))
        (run_dir / "raw_input.md").write_text("The system must track user sessions.")

        rc.lazy_init_from_run_state(run_dir, run_state)

        persisted = json.loads((run_dir / "run_state.json").read_text())
        assert persisted.get("entry_point") == "written_requirements"


def test_lazy_init_persists_entry_point_from_existing_conversation():
    """When a conversation already exists on disk, its entry_point is propagated
    back to run_state.json if run_state lacks it."""
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_105"
        run_dir.mkdir()
        # Pre-write a conversation as if it was already initialized
        rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "an app"})
        # run_state does NOT have entry_point yet
        run_state = {"run_id": "run_105", "status": "plan_only_done", "input_mode": "idea"}
        (run_dir / "run_state.json").write_text(json.dumps(run_state))

        rc.lazy_init_from_run_state(run_dir, run_state)

        persisted = json.loads((run_dir / "run_state.json").read_text())
        assert persisted.get("entry_point") == "raw_idea"


def test_backend_approve_for_idea_run_planning_gate_has_correct_entry_point(monkeypatch):
    """After approving requirements on a raw idea run, returned planning_gate must have
    entry_point=raw_idea and build_requires_approval=True (regression for smoke test bug)."""
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_103"
        run_dir.mkdir()
        run_state = {
            "run_id": "run_103",
            "status": "plan_only_done",
            "input_mode": "idea",
            # No entry_point — exactly the real-world bug condition
        }
        (run_dir / "run_state.json").write_text(json.dumps(run_state))

        # Lazily init and answer all required questions (simulates GET then answers)
        conv = rc.lazy_init_from_run_state(run_dir, run_state)
        answer_all_required(run_dir, conv)

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.post(
            "/api/runs/run_103/requirements-conversation/approve",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 200, f"Expected 200: {resp.data}"
        data = resp.get_json()
        gate = data["planning_gate"]
        assert gate["entry_point"] == "raw_idea", (
            f"planning_gate entry_point must be raw_idea, got: {gate['entry_point']}"
        )
        assert gate["build_requires_approval"] is True, (
            "raw_idea run must require planning approval"
        )
        assert gate["requirements_approved"] is True
        assert gate["architecture_approved"] is False
        assert gate["build_allowed_by_planning_gate"] is False
        assert "architecture" in gate["planning_gate_reason"].lower()


# ── AI Requirements Interviewer ─────────────────────────────────────────────────
#
# All LLM calls are mocked via monkeypatch.setattr(rc, "_call_ai_interviewer_llm", ...).
# No test in this section makes a live API call.

def _valid_ai_questions_json(n=6) -> str:
    questions = [
        {
            "id": f"q_{i}",
            "label": f"Question {i}",
            "question": f"What about aspect {i} of this product?",
            "type": "short_text",
            "options": [],
            "recommended": "",
            "required": True,
            "why": f"Determines aspect {i} of the build.",
        }
        for i in range(n)
    ]
    return json.dumps({"questions": questions})


_ATS_AI_RESPONSE = json.dumps({
    "questions": [
        {
            "id": "candidate_stages",
            "label": "Candidate stages",
            "question": "What stages should a candidate move through (e.g. Applied, Interviewing, Offer, Hired, Rejected)?",
            "type": "long_text",
            "options": [],
            "recommended": "Applied, Interviewing, Offer, Hired, Rejected",
            "required": True,
            "why": "Defines the pipeline data model and the status values the UI must support.",
        },
        {
            "id": "resume_upload",
            "label": "Resume / file uploads",
            "question": "Do hiring managers need to upload and view candidate resumes or other files?",
            "type": "yes_no",
            "options": [],
            "recommended": "Yes",
            "required": True,
            "why": "Determines whether file storage and a file viewer are part of the MVP scope.",
        },
        {
            "id": "hiring_notes",
            "label": "Hiring notes",
            "question": "Should hiring managers be able to leave notes or feedback on a candidate?",
            "type": "yes_no",
            "options": [],
            "recommended": "Yes",
            "required": True,
            "why": "Notes affect the data model and whether a comment thread UI is needed.",
        },
        {
            "id": "dashboard_metrics",
            "label": "Dashboard metrics",
            "question": "What metrics should the dashboard show (e.g. open roles, candidates per stage, time-to-hire)?",
            "type": "long_text",
            "options": [],
            "recommended": "",
            "required": False,
            "why": "Determines what aggregate queries and dashboard widgets are needed.",
        },
        {
            "id": "status_view_style",
            "label": "Status view style",
            "question": "Should candidate status be shown as a Kanban board or a simple dropdown/list view?",
            "type": "single_choice",
            "options": ["Kanban board", "Dropdown/list view"],
            "recommended": "Dropdown/list view",
            "required": True,
            "why": "Kanban boards are significantly more complex to build than a simple status dropdown.",
        },
        {
            "id": "multi_job_support",
            "label": "Multiple job postings",
            "question": "Does the MVP need to support multiple simultaneous job postings, or just one?",
            "type": "yes_no",
            "options": [],
            "recommended": "Yes",
            "required": True,
            "why": "Affects whether jobs are a first-class entity or hardcoded.",
        },
    ]
})


# 1. Template fallback when AI is unavailable

def test_template_fallback_when_ai_unavailable():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        questions = rc.generate_requirements_questions(
            "raw_idea", "", {"raw_input": "Test app"}, use_ai=True, run_dir=run_dir,
        )
        ids = {q["id"] for q in questions}
        assert "primary_user" in ids
        state = json.loads((run_dir / rc.INTERVIEWER_STATE_ARTIFACT).read_text())
        assert state["fallback_used"] is True
        assert state["mode"] == "template_fallback"


# 2. AI valid JSON is accepted as-is

def test_ai_valid_json_accepted(monkeypatch):
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: _valid_ai_questions_json(6))
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        questions = rc.generate_requirements_questions(
            "raw_idea", "", {"raw_input": "Test app"}, use_ai=True, run_dir=run_dir,
        )
        ids = {q["id"] for q in questions}
        assert ids == {f"q_{i}" for i in range(6)}
        assert "primary_user" not in ids


# 3. AI questions are persisted into requirements_questions.json

def test_ai_questions_persisted_in_conversation_state(monkeypatch):
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: _valid_ai_questions_json(6))
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test app"}, use_ai=True)
        saved = json.loads((run_dir / rc.QUESTIONS_ARTIFACT).read_text())
        ids = {q["id"] for q in saved["questions"]}
        assert ids == {f"q_{i}" for i in range(6)}
        assert saved["question_source"] == "ai"
        assert saved["question_fallback_used"] is False


# 4. ATS-specific mocked questions are domain-specific, not generic

def test_ats_specific_questions_from_mocked_ai(monkeypatch):
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: _ATS_AI_RESPONSE)
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        conv = rc.init_requirements_conversation(run_dir, "raw_idea", {
            "raw_input": "Simple ATS for small businesses to track job applicants.",
        }, use_ai=True)
        ids = {q["id"] for q in conv["questions"]}
        assert "candidate_stages" in ids
        assert "resume_upload" in ids
        assert "status_view_style" in ids
        assert "primary_user" not in ids  # generic template question must not leak through


# 5. Invalid JSON falls back to template

def test_invalid_json_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: "not valid json {{{")
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        questions = rc.generate_requirements_questions(
            "raw_idea", "", {"raw_input": "Test"}, use_ai=True, run_dir=run_dir,
        )
        ids = {q["id"] for q in questions}
        assert "primary_user" in ids
        state = json.loads((run_dir / rc.INTERVIEWER_STATE_ARTIFACT).read_text())
        assert state["fallback_used"] is True


# 6. Invalid question type falls back and sanitizes

def test_invalid_question_type_falls_back_and_sanitizes(monkeypatch):
    bad = json.dumps({"questions": [
        {"id": "bad_q", "label": "Bad", "question": "?", "type": "essay",
         "options": [], "recommended": "", "required": True, "why": "test"},
    ]})
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: bad)
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        questions = rc.generate_requirements_questions(
            "raw_idea", "", {"raw_input": "Test"}, use_ai=True, run_dir=run_dir,
        )
        ids = {q["id"] for q in questions}
        assert "bad_q" not in ids
        assert "primary_user" in ids
        state = json.loads((run_dir / rc.INTERVIEWER_STATE_ARTIFACT).read_text())
        assert state["fallback_used"] is True


# 7. Missing required field falls back

def test_missing_required_field_falls_back(monkeypatch):
    bad = json.dumps({"questions": [
        {"id": "no_why", "label": "Label", "question": "Q?", "type": "short_text",
         "options": [], "recommended": "", "required": True},  # missing "why"
    ]})
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: bad)
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        questions = rc.generate_requirements_questions(
            "raw_idea", "", {"raw_input": "Test"}, use_ai=True, run_dir=run_dir,
        )
        ids = {q["id"] for q in questions}
        assert "no_why" not in ids
        assert "primary_user" in ids


# 8. More than 10 questions is trimmed, not rejected

def test_more_than_ten_questions_trimmed(monkeypatch):
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: _valid_ai_questions_json(13))
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        questions = rc.generate_requirements_questions(
            "raw_idea", "", {"raw_input": "Test"}, use_ai=True, run_dir=run_dir,
        )
        assert len(questions) == rc._MAX_AI_QUESTIONS == 10
        state = json.loads((run_dir / rc.INTERVIEWER_STATE_ARTIFACT).read_text())
        assert state["fallback_used"] is False
        assert state["question_count"] == 10


# 9. state.json records success

def test_interviewer_state_records_success(monkeypatch):
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: _valid_ai_questions_json(6))
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        rc.generate_requirements_questions("raw_idea", "", {"raw_input": "Test"}, use_ai=True, run_dir=run_dir)
        state = json.loads((run_dir / rc.INTERVIEWER_STATE_ARTIFACT).read_text())
        assert state["mode"] == "ai"
        assert state["status"] == "success"
        assert state["fallback_used"] is False
        assert state["question_count"] == 6
        assert "generated_at" in state
        # No secrets leak into debug artifacts
        prompt_text = (run_dir / rc.INTERVIEWER_PROMPT_ARTIFACT).read_text()
        assert "sk-" not in prompt_text


# 10. state.json records fallback with a reason

def test_interviewer_state_records_fallback_with_reason():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        rc.generate_requirements_questions("raw_idea", "", {"raw_input": "Test"}, use_ai=True, run_dir=run_dir)
        state = json.loads((run_dir / rc.INTERVIEWER_STATE_ARTIFACT).read_text())
        assert state["mode"] == "template_fallback"
        assert state["status"] == "fallback"
        assert state["fallback_used"] is True
        assert state["reason"]
        assert "generated_at" in state


# 11. Approval flow works with AI-generated questions

def test_approval_flow_works_with_ai_generated_questions(monkeypatch):
    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", lambda messages: _valid_ai_questions_json(5))
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        conv = rc.init_requirements_conversation(run_dir, "raw_idea", {"raw_input": "Test app"}, use_ai=True)
        answer_all_required(run_dir, conv)
        result = rc.approve_requirements(run_dir)
        assert result["approved"] is True
        assert (run_dir / rc.APPROVED_ARTIFACT).exists()


# 12. ?question_mode=template forces template questions and skips the AI call entirely

def test_backend_question_mode_template_forces_fallback(monkeypatch):
    call_count = {"n": 0}

    def _tracking(messages):
        call_count["n"] += 1
        return _valid_ai_questions_json(6)

    monkeypatch.setattr(rc, "_call_ai_interviewer_llm", _tracking)

    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run_001"
        run_dir.mkdir()
        run_state = {"run_id": "run_001", "status": "done", "input_mode": "idea"}
        (run_dir / "run_state.json").write_text(json.dumps(run_state))

        monkeypatch.setattr(app_mod, "RUNS_DIR", Path(td))
        client = app_mod.app.test_client()
        resp = client.get("/api/runs/run_001/requirements-conversation?question_mode=template")
        assert resp.status_code == 200, f"Expected 200: {resp.data}"
        data = resp.get_json()
        ids = {q["id"] for q in data["conversation"]["questions"]}
        assert "primary_user" in ids
        assert call_count["n"] == 0, "AI must not be called when question_mode=template"
        assert data["conversation"].get("question_source") == "template"
        assert not (run_dir / rc.INTERVIEWER_STATE_ARTIFACT).exists()


# 13. Frontend TS build succeeds — validated externally via `npm run build` (see report).


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
