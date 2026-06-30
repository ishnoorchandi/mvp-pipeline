"""
Requirements Conversation — interactive requirements sign-off before architecture/build.

Provides template-based draft generation and structured question templates for three
build-capable entry points:
  raw_idea            → MVP scope questions
  written_requirements → gap-filling questions
  existing_app_upgrade → preserve/reuse/scope/risk questions

Artifact files written/read per run:
  mvp_requirements_draft.md       — draft produced from raw idea / request text
  requirements_questions.json     — structured Q&A state (primary storage)
  requirements_conversation.md    — human-readable transcript
  approved_requirements.md        — final approved requirements
  requirements_signoff_state.json — approval record (detected by planning_gate.py)

Lazy-init: conversation state is created on first access if the artifacts don't
yet exist — existing runs and plan-only flows are never touched automatically.
"""

import json
import datetime
import re
from pathlib import Path
from typing import Optional


# ── Artifact file names ───────────────────────────────────────────────────────
DRAFT_ARTIFACT = "mvp_requirements_draft.md"
QUESTIONS_ARTIFACT = "requirements_questions.json"
CONVERSATION_ARTIFACT = "requirements_conversation.md"
APPROVED_ARTIFACT = "approved_requirements.md"
SIGNOFF_ARTIFACT = "requirements_signoff_state.json"

# AI interviewer debug artifacts (written only when use_ai=True and run_dir given)
INTERVIEWER_PROMPT_ARTIFACT = "requirements_interviewer_prompt.md"
INTERVIEWER_RESPONSE_ARTIFACT = "requirements_interviewer_response.json"
INTERVIEWER_STATE_ARTIFACT = "requirements_interviewer_state.json"

# Entry points that have full conversation support
SUPPORTED_ENTRY_POINTS = frozenset({
    "raw_idea", "written_requirements", "existing_app_upgrade",
})

# Valid question types
QUESTION_TYPES = frozenset({
    "single_choice", "multi_choice", "short_text", "long_text", "yes_no",
})

# Valid requirements statuses
REQUIREMENTS_STATUSES = frozenset({
    "not_started", "draft", "questions_pending", "review", "approved",
})


# ── Draft generation ──────────────────────────────────────────────────────────

def generate_requirements_draft(
    entry_point: str,
    context: Optional[dict] = None,
) -> str:
    """
    Generate a template-based requirements draft. No LLM call required.

    context keys by entry_point:
      raw_idea:             "raw_input" (str)
      written_requirements: "requirements_text" (str)
      existing_app_upgrade: "existing_app_path" (str), "feature_request" (str),
                             "existing_app_summary" (str, optional)
    """
    ctx = context or {}
    if entry_point == "raw_idea":
        return _draft_raw_idea(ctx.get("raw_input", "(no idea text provided)"))
    if entry_point == "written_requirements":
        return _draft_written_requirements(ctx.get("requirements_text", "(no requirements text provided)"))
    if entry_point == "existing_app_upgrade":
        return _draft_existing_app_upgrade(
            app_path=ctx.get("existing_app_path", "(unknown app)"),
            feature_request=ctx.get("feature_request", "(no feature request provided)"),
            existing_app_summary=ctx.get("existing_app_summary", ""),
        )
    return _draft_raw_idea(ctx.get("raw_input", "(no idea text provided)"))


def _draft_raw_idea(raw_input: str) -> str:
    return f"""# MVP Requirements Draft

> **Status:** Draft — awaiting your answers to the requirements questions below.

## Product Summary

{raw_input}

## Target Users

*To be clarified via requirements questions.*

## Core User Workflows

*To be clarified. The single most important workflow will drive the MVP scope.*

## Must-Have Features

*Determined by answering: "What must work for the MVP to be usable at all?"*

## Nice-to-Have Later

*Features that would be great but are explicitly deferred from v1.*

## Out of Scope for V1

*Explicitly excluded to prevent scope creep.*

## Data and External Services

*Data sources (mock / local / database / external API) to be confirmed.*

## Acceptance Criteria

*Will be generated once questions are answered.*
"""


def _draft_written_requirements(requirements_text: str) -> str:
    return f"""# Normalized Requirements Draft

> **Status:** Draft — reviewing supplied requirements for gaps and completeness.

## Summary

The following requirements were provided:

{requirements_text}

## Functional Requirements

*Extracted from the above. Must-have items will be confirmed via gap questions.*

## Missing Details

*Gap-filling questions will identify what is not yet specified.*

## External Dependencies

*Any external APIs, data sources, or services not yet specified.*

## Acceptance Criteria

*Will be finalized once gap questions are answered.*
"""


def _draft_existing_app_upgrade(
    app_path: str,
    feature_request: str,
    existing_app_summary: str,
) -> str:
    app_name = Path(app_path).name if app_path else "existing app"
    summary_section = (
        f"\n{existing_app_summary}\n" if existing_app_summary
        else "*App scan summary will be available after planning step.*"
    )
    return f"""# Upgrade Requirements Draft

> **Status:** Draft — confirm scope, reuse, and non-goals before architecture planning.

## Existing App Context

**App:** `{app_name}` (`{app_path}`)

{summary_section}

## Feature Request

{feature_request}

## Preserve / Reuse

*Which existing pages, components, patterns, and conventions must be preserved?*
*To be confirmed via requirements questions.*

## Additive Changes

*Is this upgrade purely additive, or does it modify existing behavior?*
*To be confirmed.*

## Must-Have Upgrade Behavior

*The minimum set of new behaviors this upgrade must deliver.*

## Out of Scope

*What will explicitly NOT be changed in this upgrade.*

## Risk Notes

*Backend/database/auth changes carry higher risk. Scope will be confirmed.*

## Acceptance Criteria

*Will be generated once scope questions are answered.*
"""


# ── Question templates ────────────────────────────────────────────────────────

def generate_requirements_questions(
    entry_point: str,
    draft_text: str = "",
    context: Optional[dict] = None,
    use_ai: bool = True,
    run_dir: Optional[Path] = None,
) -> list[dict]:
    """
    Return a list of structured question dicts for the given entry point.
    Each dict has: id, label, question, type, options, recommended, answer,
    freeform_answer, why, required.

    When use_ai is True (default), the AI interviewer is tried first — it reads
    the run's actual idea/requirements/planning context and produces questions
    specific to that app and domain. On any failure (LLM error, malformed JSON,
    invalid schema), it transparently falls back to generate_template_requirements_questions().

    When run_dir is given, debug artifacts are written recording which path was
    used (requirements_interviewer_prompt.md / _response.json / _state.json).
    """
    ctx = context or {}
    if use_ai:
        ai_questions, debug = _try_generate_ai_requirements_questions(entry_point, draft_text, ctx)
        if ai_questions is not None:
            if run_dir is not None:
                debug["question_count"] = len(ai_questions)
                _write_interviewer_artifacts(run_dir, debug, success=True)
            return ai_questions
        if run_dir is not None:
            template_questions = generate_template_requirements_questions(entry_point, ctx)
            debug["question_count"] = len(template_questions)
            _write_interviewer_artifacts(run_dir, debug, success=False)
            return template_questions

    return generate_template_requirements_questions(entry_point, ctx)


def generate_template_requirements_questions(
    entry_point: str,
    context: Optional[dict] = None,
) -> list[dict]:
    """
    Hardcoded, deterministic fallback question templates — used when the AI
    interviewer is disabled (use_ai=False) or fails validation.
    """
    if entry_point == "raw_idea":
        return _questions_raw_idea(context or {})
    if entry_point == "written_requirements":
        return _questions_written_requirements(context or {})
    if entry_point == "existing_app_upgrade":
        return _questions_existing_app_upgrade(context or {})
    # default fallback
    return _questions_raw_idea(context or {})


# ── AI Interviewer ─────────────────────────────────────────────────────────────

_AI_INTERVIEWER_SYSTEM_PROMPT = """You are an expert product analyst conducting a structured requirements interview before an MVP gets built. You read the user's raw idea/requirements and existing planning context, then produce a short list of sharp, domain-specific clarifying questions that determine the real scope, data model, and architecture of the build.

Rules:
- Output ONLY valid JSON. No markdown, no commentary, no code fences.
- Output shape: {"questions": [ ... ]}
- Ask between 5 and 9 questions.
- Each question must be specific to the actual app/domain described in the input — not generic boilerplate like "who is the primary user" or "what is out of scope".
- Prefer questions that affect architecture, data model, sprint scope, or the core user workflow.
- Do not ask about something already answered clearly by the input.
- Do not ask about deployment or hosting unless the user mentioned it.
- Do not force auth, a database, or an external API unless the domain clearly needs it.
- Include a "recommended" default answer when it helps move the conversation forward; use "" if there is no sensible default.
- Mark required: true only for decisions that block implementation; required: false for nice-to-have clarifications.
- Every question needs a concise, practical "why" explaining what the answer affects.

Each question object must have exactly these fields:
  id          — short lowercase snake_case slug, unique within the list
  label       — short label (2-5 words)
  question    — the actual question to ask the user
  type        — one of: single_choice, multi_choice, short_text, long_text, yes_no
  options     — array of strings; required (2+) for single_choice/multi_choice, empty array otherwise
  recommended — a recommended answer string, or "" if none
  required    — boolean
  why         — one sentence explaining why this question matters
"""


def _build_ai_interviewer_prompt(entry_point: str, draft_text: str, context: dict) -> str:
    """Assemble the user-turn prompt from run context. Only includes sections
    that actually have content — keeps the prompt focused."""
    parts = [f"## Entry point\n{entry_point}\n"]

    raw_input = (
        context.get("raw_input")
        or context.get("requirements_text")
        or context.get("feature_request")
        or ""
    )
    if raw_input.strip():
        parts.append(f"## User's raw idea / requirements\n{raw_input.strip()}\n")

    if context.get("existing_app_path"):
        parts.append(f"## Existing app path\n{context['existing_app_path']}\n")
    if (context.get("existing_app_summary") or "").strip():
        parts.append(f"## Existing app summary\n{context['existing_app_summary'].strip()}\n")

    if (context.get("mvp_scope") or "").strip():
        parts.append(f"## MVP scope (planning artifact)\n{context['mvp_scope'].strip()}\n")
    if (context.get("clean_requirements") or "").strip():
        parts.append(f"## Clean requirements (planning artifact)\n{context['clean_requirements'].strip()}\n")
    if (context.get("mvp_spec") or "").strip():
        parts.append(f"## MVP spec (planning artifact)\n{context['mvp_spec'].strip()}\n")

    if (draft_text or "").strip():
        parts.append(f"## Draft requirements (auto-generated, awaiting your interview questions)\n{draft_text.strip()}\n")

    parts.append(
        "## Task\n"
        "Read everything above and produce 5-9 sharp, domain-specific clarifying "
        "questions that determine the real scope, data model, and core workflow "
        "of this specific MVP. Follow the system instructions exactly. Output JSON only."
    )
    return "\n".join(parts)


def _call_ai_interviewer_llm(messages: list[dict]) -> str:
    """
    Call the LLM using the same client/model pattern as pipeline_mvp_builder.gpt()
    (OpenAI chat completions, GPT_MODEL from config). Imports openai/config lazily
    so a missing OPENAI_API_KEY never breaks module import — only AI question
    generation, which the caller catches and falls back to templates for.
    """
    from openai import OpenAI
    import config as config_mod
    client = OpenAI(api_key=config_mod.OPENAI_API_KEY)
    resp = client.chat.completions.create(model=config_mod.GPT_MODEL, messages=messages)
    return resp.choices[0].message.content.strip()


def _parse_ai_json_response(raw_response: str) -> object:
    """Parse the LLM's JSON response, defensively stripping markdown code fences
    if the model wrapped its output despite instructions not to."""
    text = (raw_response or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_MAX_AI_QUESTIONS = 10


def _validate_ai_questions(raw: object) -> list[dict]:
    """
    Validate and normalize raw AI JSON into the canonical question dict shape
    (same shape produced by _q()). Raises ValueError with a human-readable
    reason on any structural problem — callers must catch and fall back.
    """
    if not isinstance(raw, dict):
        raise ValueError("AI response is not a JSON object")
    questions = raw.get("questions")
    if not isinstance(questions, list) or len(questions) == 0:
        raise ValueError("AI response missing a non-empty 'questions' list")

    normalized: list[dict] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(questions):
        if not isinstance(item, dict):
            raise ValueError(f"question[{idx}] is not an object")

        qid = item.get("id")
        label = item.get("label")
        question_text = item.get("question")
        qtype = item.get("type")
        why = item.get("why")
        required = item.get("required")

        if not isinstance(qid, str) or not _SLUG_RE.match(qid):
            raise ValueError(f"question[{idx}] has an invalid id: {qid!r}")
        if qid in seen_ids:
            raise ValueError(f"duplicate question id: {qid}")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"question[{idx}] is missing a label")
        if not isinstance(question_text, str) or not question_text.strip():
            raise ValueError(f"question[{idx}] is missing question text")
        if qtype not in QUESTION_TYPES:
            raise ValueError(f"question[{idx}] has an invalid type: {qtype!r}")
        if not isinstance(why, str) or not why.strip():
            raise ValueError(f"question[{idx}] is missing 'why'")
        if not isinstance(required, bool):
            raise ValueError(f"question[{idx}] is missing a boolean 'required'")

        options = item.get("options") or []
        if not isinstance(options, list):
            raise ValueError(f"question[{idx}] options must be a list")
        options = [str(o) for o in options]
        if qtype in ("single_choice", "multi_choice") and len(options) < 2:
            raise ValueError(f"question[{idx}] type {qtype} requires at least 2 options")

        recommended = item.get("recommended") or ""
        if not isinstance(recommended, str):
            recommended = str(recommended)

        seen_ids.add(qid)
        normalized.append(_q(
            qid, label.strip(), question_text.strip(), qtype,
            options=options, recommended=recommended, why=why.strip(), required=required,
        ))

    # Defensive cap — the prompt asks for 5-9, but never trust the model fully.
    if len(normalized) > _MAX_AI_QUESTIONS:
        normalized = normalized[:_MAX_AI_QUESTIONS]

    return normalized


def generate_ai_requirements_questions(
    entry_point: str,
    draft_text: str = "",
    context: Optional[dict] = None,
    _debug: Optional[dict] = None,
) -> list[dict]:
    """
    Call the AI interviewer and return validated, structured question dicts.

    Raises (ValueError / json.JSONDecodeError / any LLM client exception) on
    failure — callers must catch and fall back to
    generate_template_requirements_questions(). The optional _debug dict is
    populated with the prompt and raw LLM response as they become available,
    used internally by generate_requirements_questions() to write debug
    artifacts regardless of success or failure.
    """
    ctx = context or {}
    prompt = _build_ai_interviewer_prompt(entry_point, draft_text, ctx)
    if _debug is not None:
        _debug["prompt"] = prompt
    messages = [
        {"role": "system", "content": _AI_INTERVIEWER_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    raw_response = _call_ai_interviewer_llm(messages)
    if _debug is not None:
        _debug["raw_response"] = raw_response
    parsed = _parse_ai_json_response(raw_response)
    return _validate_ai_questions(parsed)


def _try_generate_ai_requirements_questions(
    entry_point: str,
    draft_text: str,
    context: dict,
) -> tuple[Optional[list[dict]], dict]:
    """
    Attempt AI question generation, catching every failure mode (LLM error,
    invalid JSON, schema violation) so the caller can safely fall back.
    Returns (questions, debug). questions is None on any failure; debug
    always has 'prompt' and 'reason' (None on success).
    """
    debug: dict = {"prompt": "", "raw_response": None, "reason": None}
    try:
        questions = generate_ai_requirements_questions(entry_point, draft_text, context, _debug=debug)
        return questions, debug
    except Exception as exc:
        debug["reason"] = str(exc)
        return None, debug


def _write_interviewer_artifacts(run_dir: Path, debug: dict, success: bool) -> None:
    """Write requirements_interviewer_{prompt.md,response.json,state.json}.
    Never raises — debug-artifact failures must not break question generation.
    Never writes API keys or secrets (only prompt text and model output)."""
    try:
        run_dir = Path(run_dir)
        (run_dir / INTERVIEWER_PROMPT_ARTIFACT).write_text(debug.get("prompt") or "", encoding="utf-8")
        _write_json(run_dir / INTERVIEWER_RESPONSE_ARTIFACT, {"raw_response": debug.get("raw_response")})

        if success:
            state = {
                "mode": "ai",
                "status": "success",
                "fallback_used": False,
                "question_count": debug.get("question_count", 0),
                "generated_at": _now_iso(),
            }
        else:
            state = {
                "mode": "template_fallback",
                "status": "fallback",
                "fallback_used": True,
                "reason": debug.get("reason") or "AI question generation unavailable",
                "question_count": debug.get("question_count", 0),
                "generated_at": _now_iso(),
            }
        _write_json(run_dir / INTERVIEWER_STATE_ARTIFACT, state)
    except Exception:
        pass


def _q(
    id: str,
    label: str,
    question: str,
    type: str,
    options: Optional[list] = None,
    recommended: str = "",
    why: str = "",
    required: bool = True,
) -> dict:
    return {
        "id": id,
        "label": label,
        "question": question,
        "type": type,
        "options": options or [],
        "recommended": recommended,
        "answer": None,
        "freeform_answer": "",
        "why": why,
        "required": required,
    }


def _questions_raw_idea(context: dict) -> list[dict]:
    return [
        _q(
            "primary_user",
            "Primary user",
            "Who is the primary user of this MVP?",
            "short_text",
            why="Knowing the primary user shapes every UX and workflow decision.",
        ),
        _q(
            "core_workflow",
            "Core workflow",
            "What is the single most important user workflow for the MVP?",
            "long_text",
            why="One golden path keeps the MVP focused and buildable in one sprint.",
        ),
        _q(
            "user_accounts",
            "User accounts in v1",
            "Should users need accounts / login in v1?",
            "yes_no",
            recommended="No",
            why="Auth adds significant complexity. Deferring it keeps the first sprint lean.",
        ),
        _q(
            "data_source",
            "Data source",
            "What should the MVP use for data?",
            "single_choice",
            options=["Mock data", "Local JSON file", "Database seed data", "External API"],
            recommended="Mock data",
            why="Mock data keeps the first MVP reliable without requiring paid APIs or a seeded database.",
        ),
        _q(
            "must_have_features",
            "Must-have features",
            "Which features MUST be present for the MVP to be usable?",
            "long_text",
            why="Distinguishes the non-negotiable core from nice-to-haves.",
        ),
        _q(
            "nice_to_have",
            "Nice-to-have (later)",
            "Which features would be great but should be explicitly deferred from v1?",
            "long_text",
            recommended="",
            why="Explicit deferral prevents scope creep during the build.",
            required=False,
        ),
        _q(
            "out_of_scope",
            "Out of scope for v1",
            "What is explicitly out of scope for v1?",
            "long_text",
            why="Stating non-goals prevents the build from expanding unexpectedly.",
        ),
    ]


def _questions_written_requirements(context: dict) -> list[dict]:
    return [
        _q(
            "must_have_v1",
            "Must-have for v1",
            "Which of the listed features are must-have for v1?",
            "long_text",
            why="Prioritizes the non-negotiable features so the sprint stays focused.",
        ),
        _q(
            "defer_later",
            "Defer to later",
            "Which features should be explicitly deferred from v1?",
            "long_text",
            required=False,
            why="Explicit deferral prevents the first sprint from becoming too large.",
        ),
        _q(
            "auth_included",
            "Auth / database / API included?",
            "Do the requirements include auth, database, or external API calls in v1?",
            "yes_no",
            recommended="No",
            why="Auth and API integrations multiply complexity. Knowing this up front shapes the architecture.",
        ),
        _q(
            "non_goals",
            "Non-goals",
            "What are the explicit non-goals for this build?",
            "long_text",
            required=False,
            why="Non-goals are as important as goals for scoping a buildable sprint.",
        ),
        _q(
            "external_dependencies",
            "External dependencies",
            "Are there external data sources, APIs, or services this MVP depends on?",
            "long_text",
            required=False,
            why="External dependencies need mocking or integration planning before build.",
        ),
    ]


def _questions_existing_app_upgrade(context: dict) -> list[dict]:
    return [
        _q(
            "preserve_pages",
            "Pages/components to preserve",
            "Which existing pages, components, or flows must be preserved exactly as-is?",
            "long_text",
            why="Explicit preservation scope prevents accidental regressions.",
        ),
        _q(
            "additive_only",
            "Additive-only change",
            "Should this upgrade be strictly additive — no existing behavior changed or removed?",
            "yes_no",
            recommended="Yes",
            why="Additive-only upgrades are safer, faster, and easier to review.",
        ),
        _q(
            "backend_db_allowed",
            "Backend / database changes allowed",
            "Are backend or database schema changes allowed in this upgrade?",
            "yes_no",
            recommended="No",
            why="Backend and schema changes carry higher risk. Frontend-only upgrades are safer for the first sprint.",
        ),
        _q(
            "frontend_first",
            "Frontend-first sprint",
            "Should the first buildable sprint be frontend-only?",
            "yes_no",
            recommended="Yes",
            why="Frontend-only sprints are faster to build, review, and validate.",
        ),
        _q(
            "reuse_patterns",
            "Patterns to reuse",
            "Which current app patterns, component libraries, or conventions must the upgrade follow?",
            "long_text",
            required=False,
            why="Reusing established patterns keeps the upgrade consistent with the existing codebase.",
        ),
        _q(
            "out_of_scope",
            "Out of scope for this upgrade",
            "What is explicitly out of scope for this upgrade?",
            "long_text",
            why="Non-goals prevent the upgrade from expanding beyond the agreed feature set.",
        ),
        _q(
            "new_vs_existing_files",
            "New modules or extend existing files",
            "Should this feature create new modules or extend existing files?",
            "single_choice",
            options=["Extend existing files", "Create new modules", "Both — as appropriate"],
            recommended="Extend existing files",
            why="Extending existing files is lower-risk; new modules are cleaner for large features.",
            required=False,
        ),
    ]


# ── State management ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_requirements_conversation(run_dir: Path) -> Optional[dict]:
    """Load conversation state from requirements_questions.json, or None if absent."""
    return _safe_read_json(Path(run_dir) / QUESTIONS_ARTIFACT)


def _persist_entry_point(run_dir: Path, entry_point: str) -> None:
    """Write entry_point into run_state.json if it is absent or unknown.

    Called after lazy init infers the entry_point so that subsequent calls to
    planning_gate.build_planning_gate_from_run_state see the correct value even
    without reloading state from disk in the caller.  Never raises.
    """
    if not entry_point or entry_point == "unknown":
        return
    state_path = run_dir / "run_state.json"
    if not state_path.exists():
        return
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not data.get("entry_point") or data["entry_point"] == "unknown":
            data["entry_point"] = entry_point
            state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def init_requirements_conversation(
    run_dir: Path,
    entry_point: str,
    context: Optional[dict] = None,
    use_ai: bool = True,
) -> dict:
    """
    Initialize and persist a fresh requirements conversation for a run.
    Writes mvp_requirements_draft.md, requirements_questions.json,
    and requirements_conversation.md.

    If the conversation already exists, returns the existing state unchanged.
    """
    run_dir = Path(run_dir)
    existing = load_requirements_conversation(run_dir)
    if existing is not None:
        return existing

    ctx = context or {}
    draft = generate_requirements_draft(entry_point, ctx)
    questions = generate_requirements_questions(entry_point, draft, ctx, use_ai=use_ai, run_dir=run_dir)

    # Write draft artifact
    (run_dir / DRAFT_ARTIFACT).write_text(draft, encoding="utf-8")

    # Surface AI-vs-template provenance in the persisted state so the frontend
    # can render the interviewer badge from the normal conversation response,
    # without a second fetch. Read back the interviewer state artifact that
    # generate_requirements_questions() just wrote (only happens when use_ai=True).
    question_source = "template"
    question_fallback_used = False
    question_fallback_reason = None
    if use_ai:
        interviewer_state = _safe_read_json(run_dir / INTERVIEWER_STATE_ARTIFACT)
        if interviewer_state is not None:
            question_source = interviewer_state.get("mode", "template")
            question_fallback_used = bool(interviewer_state.get("fallback_used", False))
            question_fallback_reason = interviewer_state.get("reason")

    state = {
        "entry_point": entry_point,
        "requirements_status": "questions_pending",
        "questions": questions,
        "answers": {},
        "draft_requirements_artifact": DRAFT_ARTIFACT,
        "approved_requirements_artifact": None,
        "requirements_approved": False,
        "question_source": question_source,
        "question_fallback_used": question_fallback_used,
        "question_fallback_reason": question_fallback_reason,
        "updated_at": _now_iso(),
    }
    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)
    return state


def lazy_init_from_run_state(run_dir: Path, run_state: dict, use_ai: bool = True) -> dict:
    """
    Lazily initialize a requirements conversation from an existing run's state.
    Called when the frontend opens the requirements conversation for a run that
    predates the conversation system.

    use_ai controls whether the AI interviewer is attempted (default True);
    pass False to force the deterministic template questions (e.g. ?question_mode=template).
    """
    run_dir = Path(run_dir)
    existing = load_requirements_conversation(run_dir)
    if existing is not None:
        _persist_entry_point(run_dir, existing.get("entry_point", ""))
        return existing

    # Infer entry point from run_state
    if run_state.get("bugfix_mode"):
        entry_point = "bugfix"
    elif run_state.get("backend_inventory_mode"):
        entry_point = "backend_inventory"
    elif run_state.get("mode") == "existing_app_upgrade" or run_state.get("upgrade_mode"):
        entry_point = "existing_app_upgrade"
    elif run_state.get("entry_point") in SUPPORTED_ENTRY_POINTS:
        entry_point = run_state.get("entry_point")
    elif run_state.get("input_mode") == "requirements":
        entry_point = "written_requirements"
    elif run_state.get("input_mode") == "idea":
        entry_point = "raw_idea"
    else:
        entry_point = "raw_idea"

    if entry_point not in SUPPORTED_ENTRY_POINTS:
        # Unsupported entry points — return a minimal "not applicable" state
        return {
            "entry_point": entry_point,
            "requirements_status": "not_applicable",
            "questions": [],
            "answers": {},
            "draft_requirements_artifact": None,
            "approved_requirements_artifact": None,
            "requirements_approved": False,
            "question_source": "template",
            "question_fallback_used": False,
            "question_fallback_reason": None,
            "updated_at": _now_iso(),
        }

    # Build context from available artifacts
    ctx = {}
    raw_input_path = run_dir / "raw_input.md"
    feature_request_path = run_dir / "feature_request.md"
    feature_request_input_path = run_dir / "feature_request_input.md"
    existing_app_summary_path = run_dir / "existing_app_summary.md"

    if entry_point == "existing_app_upgrade":
        ctx["existing_app_path"] = run_state.get("existing_app_path") or run_state.get("existing_app") or ""
        for fr_path in (feature_request_path, feature_request_input_path, raw_input_path):
            if fr_path.exists():
                ctx["feature_request"] = fr_path.read_text(encoding="utf-8")
                break
        if existing_app_summary_path.exists():
            ctx["existing_app_summary"] = existing_app_summary_path.read_text(encoding="utf-8")
    elif entry_point == "written_requirements":
        for p in (raw_input_path, feature_request_path):
            if p.exists():
                ctx["requirements_text"] = p.read_text(encoding="utf-8")
                break
    else:  # raw_idea
        if raw_input_path.exists():
            ctx["raw_input"] = raw_input_path.read_text(encoding="utf-8")

    # Universal planning context, if the pipeline has produced it by this point —
    # used only by the AI interviewer to ask sharper, more specific questions.
    for ctx_key, filename in (
        ("mvp_scope", "mvp_scope.md"),
        ("clean_requirements", "clean_requirements.md"),
        ("mvp_spec", "mvp_spec.md"),
    ):
        artifact_path = run_dir / filename
        if artifact_path.exists():
            ctx[ctx_key] = artifact_path.read_text(encoding="utf-8")

    _persist_entry_point(run_dir, entry_point)
    return init_requirements_conversation(run_dir, entry_point, ctx, use_ai=use_ai)


def save_answer(
    run_dir: Path,
    question_id: str,
    answer: Optional[str],
    freeform_answer: str = "",
) -> dict:
    """
    Update a single question's answer in requirements_questions.json.
    Returns the updated conversation state.
    Raises ValueError if the question_id is not found.
    """
    run_dir = Path(run_dir)
    state = load_requirements_conversation(run_dir)
    if state is None:
        raise ValueError(f"No requirements conversation found in {run_dir}")

    found = False
    for q in state["questions"]:
        if q["id"] == question_id:
            q["answer"] = answer
            q["freeform_answer"] = freeform_answer or ""
            found = True
            break
    if not found:
        raise ValueError(f"Question '{question_id}' not found in conversation")

    # Sync answers dict
    state["answers"][question_id] = answer
    if freeform_answer:
        state.setdefault("freeform_answers", {})[question_id] = freeform_answer

    state["requirements_status"] = "questions_pending"
    state["updated_at"] = _now_iso()

    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)
    return state


def approve_requirements(run_dir: Path) -> dict:
    """
    Approve requirements for this run.

    Validates that all required questions have answers.
    Writes approved_requirements.md and requirements_signoff_state.json.
    Returns {"approved": bool, "state": conversation_state, "error": str or None}.
    """
    run_dir = Path(run_dir)
    state = load_requirements_conversation(run_dir)
    if state is None:
        return {"approved": False, "state": None, "error": "No requirements conversation found."}

    # Check required questions
    unanswered = [
        q["id"] for q in state["questions"]
        if q.get("required", True) and not q.get("answer") and not q.get("freeform_answer")
    ]
    if unanswered:
        return {
            "approved": False,
            "state": state,
            "error": f"Required questions not yet answered: {', '.join(unanswered)}",
        }

    # Generate approved requirements doc
    approved_md = _build_approved_requirements_md(state)
    (run_dir / APPROVED_ARTIFACT).write_text(approved_md, encoding="utf-8")

    # Write signoff state (detected by planning_gate.py)
    now = _now_iso()
    signoff = {
        "status": "approved",
        "approved_at": now,
        "approved_by": "user",
        "approved_requirements_artifact": APPROVED_ARTIFACT,
        "source_draft_artifact": state.get("draft_requirements_artifact") or DRAFT_ARTIFACT,
    }
    _write_json(run_dir / SIGNOFF_ARTIFACT, signoff)

    # Update conversation state
    state["requirements_status"] = "approved"
    state["requirements_approved"] = True
    state["approved_requirements_artifact"] = APPROVED_ARTIFACT
    state["updated_at"] = now
    _write_json(run_dir / QUESTIONS_ARTIFACT, state)
    _write_conversation_md(run_dir, state)

    return {"approved": True, "state": state, "error": None}


def _build_approved_requirements_md(state: dict) -> str:
    """Generate the approved_requirements.md from answered questions + draft."""
    entry_point = state.get("entry_point", "unknown")
    ep_label = entry_point.replace("_", " ").title()
    lines = [
        f"# Approved Requirements — {ep_label}",
        "",
        f"> **Status:** Approved  ",
        f"> **Approved at:** {_now_iso()}",
        "",
        "## Requirements Summary",
        "",
    ]

    for q in state.get("questions", []):
        ans = q.get("answer") or q.get("freeform_answer") or ""
        if not ans:
            continue
        lines.append(f"### {q.get('label', q['id'])}")
        lines.append("")
        lines.append(f"**Question:** {q['question']}")
        lines.append("")
        lines.append(f"**Answer:** {ans}")
        if q.get("freeform_answer") and q.get("freeform_answer") != ans:
            lines.append("")
            lines.append(f"**Notes:** {q['freeform_answer']}")
        lines.append("")

    lines += [
        "---",
        "",
        "*This document was generated from the requirements conversation sign-off.*",
        "*Architecture planning may now proceed.*",
    ]
    return "\n".join(lines)


def _write_conversation_md(run_dir: Path, state: dict) -> None:
    """Write a human-readable markdown transcript of the conversation."""
    entry_point = state.get("entry_point", "unknown")
    ep_label = entry_point.replace("_", " ").title()
    status = state.get("requirements_status", "unknown")
    lines = [
        f"# Requirements Conversation — {ep_label}",
        "",
        f"**Status:** {status.replace('_', ' ').title()}",
        f"**Updated:** {state.get('updated_at', '')}",
        "",
        "---",
        "",
        "## Questions and Answers",
        "",
    ]
    for q in state.get("questions", []):
        ans = q.get("answer") or q.get("freeform_answer") or "_Not yet answered_"
        req_marker = " *(required)*" if q.get("required", True) else ""
        lines.append(f"### {q.get('label', q['id'])}{req_marker}")
        lines.append("")
        lines.append(f"**Q:** {q['question']}")
        if q.get("why"):
            lines.append(f"*Why:* {q['why']}")
        lines.append("")
        lines.append(f"**A:** {ans}")
        lines.append("")

    (run_dir / CONVERSATION_ARTIFACT).write_text("\n".join(lines), encoding="utf-8")


def get_unanswered_required(state: dict) -> list[str]:
    """Return list of required question IDs that have no answer."""
    return [
        q["id"] for q in state.get("questions", [])
        if q.get("required", True) and not q.get("answer") and not q.get("freeform_answer")
    ]
