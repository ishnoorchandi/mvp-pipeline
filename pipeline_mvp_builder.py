#!/usr/bin/env python3
"""
MVP Pipeline — Automated MVP Builder
======================================
Input:  Pasted MVP idea, Jira ticket (--jira PROJ-123), or .md file (--input)
Output: Built local MVP + full audit trail in runs/run_NNN/

Pipeline:
  1. GPT-mini   → MVP product spec
  2. GPT-mini   → Claude Code build prompt
  3. Claude Code → builds the MVP locally
  4. Smoke checks → install / build / start / API / DB checks
  5. DeepSeek    → red-team / attack review
  6. Claude Code → fixes issues
  7. Repeat 4-6 until approved or max iterations
  8. GPT-mini    → final judge + handoff summary

Usage:
    python pipeline_mvp_builder.py
    python pipeline_mvp_builder.py --input path/to/idea.md
    python pipeline_mvp_builder.py --jira PROJ-123
    python pipeline_mvp_builder.py --resume runs/run_001
    python pipeline_mvp_builder.py --no-deepseek
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from openai import OpenAI

from config import (
    OPENAI_API_KEY, GPT_MODEL, GPT4O_MODEL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    CLAUDE_CODE_CMD, CLAUDE_TIMEOUT,
    MAX_FIX_ITERATIONS, MAX_GOVERNANCE_ITERATIONS,
    RUNS_DIR, SMOKE_DIR,
    CODE_EXTS,
)

# ── Clients ────────────────────────────────────────────────────────────────────
client = OpenAI(api_key=OPENAI_API_KEY)

deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
) if DEEPSEEK_API_KEY else None


# ── Progress logger ────────────────────────────────────────────────────────────
# Writes clean lines to the log — no spinner frames, no \r spam.
# Heartbeat prints one "still running" line every 10 seconds so the terminal
# shows activity during long steps (Claude Code build can take minutes).

class Progress:
    def __init__(self):
        self._stop = True
        self._step = ""
        self._detail = ""
        self._t0 = time.time()
        self._thread = None

    def _heartbeat(self):
        last_print = 0
        while not self._stop:
            elapsed = int(time.time() - self._t0)
            if elapsed - last_print >= 10:
                m, s = divmod(elapsed, 60)
                ts = f"{m}m {s:02d}s" if m else f"{s}s"
                detail = f"  —  {self._detail}" if self._detail else ""
                print(f"  ⋯  {self._step}{detail}  [{ts}]", flush=True)
                last_print = elapsed
            time.sleep(1)

    def start(self, step: str, detail: str = ""):
        self._step = step
        self._detail = detail
        self._t0 = time.time()
        self._stop = False
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()

    def update(self, detail: str):
        self._detail = detail

    def done(self, msg: str = ""):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=1.5)
        elapsed = int(time.time() - self._t0)
        m, s = divmod(elapsed, 60)
        ts = f"{m}m {s:02d}s" if m else f"{s}s"
        print(f"  ✓  {msg or self._step}  [{ts}]", flush=True)


progress = Progress()


# ── Run management ─────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def next_run_id() -> str:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        [d.name for d in RUNS_DIR.iterdir() if d.is_dir() and re.match(r"run_\d+", d.name)]
    )
    if not existing:
        return "run_001"
    last = int(existing[-1].split("_")[1])
    return f"run_{last + 1:03d}"


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def save_artifact(run_id: str, filename: str, content: str):
    d = run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(content, encoding="utf-8")
    _update_state(run_id, {})


def _state_path(run_id: str) -> Path:
    return run_dir(run_id) / "run_state.json"


def load_state(run_id: str) -> dict:
    p = _state_path(run_id)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _update_state(run_id: str, updates: dict):
    p = _state_path(run_id)
    d = run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    state = load_state(run_id)
    state.update(updates)
    state["artifacts"] = [
        f.name for f in sorted(d.iterdir())
        if f.is_file() and f.name != "run_state.json"
    ]
    p.write_text(json.dumps(state, indent=2, default=str))


def init_run(run_id: str, raw_input: str):
    _update_state(run_id, {
        "run_id": run_id,
        "created": _now(),
        "pipeline_started_at": _now(),
        "status": "started",
        "current_step": "queued",
        "fix_iteration": 0,
        "step_timings": {},
        "log": [],
    })
    save_artifact(run_id, "raw_input.md", raw_input)


def log_event(run_id: str, event: str, detail: str = ""):
    state = load_state(run_id)
    log = state.get("log", [])
    log.append({"time": _now(), "event": event, "detail": detail[:2000]})
    _update_state(run_id, {"log": log})


def record_step_time(run_id: str, step_key: str, t0: float):
    elapsed = int(time.time() - t0)
    state = load_state(run_id)
    timings = state.get("step_timings", {})
    timings[step_key] = elapsed
    _update_state(run_id, {"step_timings": timings})


# ── LLM helpers ───────────────────────────────────────────────────────────────

def gpt(messages: list[dict]) -> str:
    """GPT-4o-mini call (fast, cheap — used for most planning/judgment steps)."""
    resp = client.chat.completions.create(model=GPT_MODEL, messages=messages)
    return resp.choices[0].message.content.strip()


def gpt4o(messages: list[dict]) -> str:
    """GPT-4o call (stronger reasoning — used for legal/privacy governance review)."""
    resp = client.chat.completions.create(model=GPT4O_MODEL, messages=messages)
    return resp.choices[0].message.content.strip()


def deepseek_chat(messages: list[dict]) -> str:
    """Generic DeepSeek chat call. Returns an error string if client unavailable."""
    if not deepseek_client:
        return "DeepSeek API key not set — review skipped."
    try:
        resp = deepseek_client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"DeepSeek API error: {e}"


# ── Step 0: Planning Artifacts ────────────────────────────────────────────────
# Mode detection is rule-based (deterministic) — no GPT call.
# Normalization / scoping below uses GPT-4o-mini, but never invents a product
# when requirements already exist.

_STRUCTURED_MARKERS = (
    "## acceptance criteria", "## description", "## requirements",
    "acceptance criteria:", "user story", "as a user,", "as a user ",
)


def detect_mode(raw_input: str, jira_used: bool = False, override: str | None = None) -> str:
    """Return 'requirements' or 'idea'. Deterministic — no GPT call."""
    if override in ("requirements", "idea"):
        return override
    if jira_used:
        return "requirements"
    lower = raw_input.lower()
    if any(marker in lower for marker in _STRUCTURED_MARKERS):
        return "requirements"
    if len(raw_input.split()) >= 50:
        return "requirements"
    return "idea"


# ── Negative-constraint detection (deterministic) ─────────────────────────────
# Catches things the requirements explicitly EXCLUDE (e.g. "no backend") so that
# later GPT-generated planning artifacts can be told about them, and validated
# against them afterward.

_CONSTRAINT_DETECT_PATTERNS = {
    "frontend_only":        [r"frontend[\s-]?only", r"client[\s-]?only"],
    "no_backend":           [r"no backend", r"without (a |an )?backend", r"no server\b"],
    "no_database":          [r"no database", r"without (a |an )?database", r"no db\b"],
    "no_login":             [r"no login", r"without (a )?login"],
    "no_auth":              [r"no auth(entication)?\b", r"without auth(entication)?"],
    "no_api":               [r"no api\b", r"without (an )?api"],
    "no_external_services": [r"no external service", r"no third[\s-]?party"],
    "no_persistence":       [r"no persistence", r"no storage\b", r"no data storage"],
}

# Human-readable directives injected into GPT prompts when a constraint is active.
_CONSTRAINT_DIRECTIVES = {
    "frontend_only": "This MVP is FRONTEND-ONLY. Do not invent a backend, server, or any "
                      "backend framework (Flask/Express/FastAPI/Django).",
    "no_backend":    "Do NOT include a backend, backend folder, or backend framework "
                      "(Flask/Express/FastAPI/Django).",
    "no_database":   "Do NOT include a database of any kind (no PostgreSQL/SQLite/MySQL/MongoDB), "
                      "no schema, and no psql/db checks.",
    "no_api":        "Do NOT include API endpoints, REST routes, or curl checks against a backend API.",
    "no_login":      "Do NOT include a login or sign-in system.",
    "no_auth":       "Do NOT include any authentication system (no JWT, OAuth, sessions, auth middleware).",
    "no_external_services": "Do NOT integrate any external/third-party services.",
    "no_persistence": "Do NOT add any persistence or storage layer.",
}

# Forbidden terms to scan for per constraint, used by check_requirements_consistency().
# Entries prefixed with "regex:" are matched as raw regex instead of an escaped literal —
# used when a plain word (like "express") is also common English and needs surrounding
# context (e.g. "Express.js", "npm install express") to disambiguate it from a false hit.
_CONSTRAINT_FORBIDDEN_TERMS = {
    "no_backend":    ["backend", "flask", "fastapi", "django",
                       "node server", "server.js", "app.py",
                       r"regex:express\.js",
                       r"regex:express\s+server",
                       r"regex:npm\s+(install|i)\s+express",
                       r"regex:require\(\s*['\"]express['\"]\s*\)",
                       r"regex:import\s+express\b",
                       r"regex:set\s*up\s+express\b",
                       r"regex:use\s+express\b",
                       r"regex:backend:\s*express\b",
                       r"regex:express\(\)",
                       r"regex:express\s+(app|framework|backend|route|router|middleware)"],
    "no_database":   ["postgresql", "postgres", "sqlite", "mysql", "mongodb", "database",
                       "psql", "db.sqlite3", "create table"],
    "no_api":        [r"regex:\bapi\b", "curl http"],
    "no_login":      ["login", "sign-in page", "sign in page"],
    "no_auth":       ["authentication", "jwt", "oauth", "session token",
                       "auth middleware", "auth system"],
    "no_external_services": ["third-party api", "external api", "stripe", "firebase", "auth0"],
    "no_persistence": ["save to database", "persist to disk", "persistent storage",
                        "data storage layer"],
}

_NEGATION_RE = re.compile(
    r"\b(no|not|without|never|none|n/a|isn't|is not|doesn't|does not|"
    r"not used|not needed|not required|not applicable|excluded|excluding)\b",
    re.IGNORECASE,
)

# When the term "API" is matched, check the immediately preceding word(s).  If they
# name a well-known *frontend* or *browser* API (e.g. "React Context API",
# "Browser API"), the match is a frontend concept, not a backend endpoint — skip it.
_FRONTEND_API_MODIFIER_RE = re.compile(
    r"\b(react\s+context|context|browser|web|canvas|dom|history|geolocation|"
    r"file|audio|video|speech|payment|intersection\s+observer|"
    r"notification|credential|clipboard|performance|url|storage|"
    r"pointer|keyboard|touch|gamepad|resize\s+observer)\s*$",
    re.IGNORECASE,
)

# Whole-line "safe" phrasing — if any of these match anywhere in a line, the line is
# treated as safely describing an EXCLUSION, not an instruction to build the forbidden
# thing. Checked before the generic forbidden-term scan.
_SAFE_PHRASE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"no\s+[\w\s/,-]{0,40}\b(is|are)\s+(required|needed|included|used|necessary)\b",
        r"without\s+(a |an )?(backend|database|api|login|auth\w*)",
        r"\bnot\s+(included|required|needed|applicable|used)\b",
        r"\bexcluded\b",
        r"\bexcluding\b",
        r"\bnone\s+(is|are)?\s*(required|needed|expected)?\b",
        r"\bn/a\b",
        r"\bnone\s+expected\b",
        r"\bno\s+matches?\s+expected\b",
        r"\bconfirms?\s+(no|there is no|none)\b",
        # "No inclusion of X" — a prohibition, regardless of how far X is from "no".
        r"\bno\s+inclusion\s+of\b",
        # "do not / does not / must not / should not + verb" — a prohibition, wherever
        # the forbidden term ends up landing in the rest of the sentence.
        r"\b(do|does|must|should)\s+not\s+(include|add|implement|use|build|create|set\s*up|"
        r"invent|introduce)\b",
        r"\b(don't|doesn't|shouldn't|mustn't)\s+(include|add|implement|use|build|create|set\s*up|"
        r"invent|introduce)\b",
        # Sprint-mode guardrail boilerplate (_constraints_to_prompt_text and the selected
        # sprint build prompt): telling Claude Code to disregard a conflicting spec section,
        # or stating that constraints win, is a guardrail — not an instruction to build the
        # forbidden thing.
        r"\bconflicts?\s+with\b[\w\s/,-]{0,60}\bconstraints?\b",
        r"\bconstraints?\s+take\s+precedence\b",
        r"\bif\s+(it\s+is\s+|they\s+are\s+)?forbidden\b",
        r"\bif\s+forbidden\b",
    )
]

# Explicit imperative instructions to BUILD the forbidden thing — always a violation,
# even if a safe-sounding word appears elsewhere on the same line, and even if the
# line sits under an Out of Scope / Excluded heading (an instruction is not an exclusion).
_BLOCKED_INSTRUCTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\b(build|create|set\s*up|add|implement)\s+(a |an |the )?backend\b",
        r"\b(create|build|set\s*up)\s+(a |an )?(api\s+(routes?|endpoints?|calls?|requests?)|backend)\b",
        r"\b(use|create|build|set\s*up)\s+(a |an )?(postgresql|postgres|sqlite|mysql|mongodb|database)\b",
        r"\brun\s+psql\b",
        r"\b(use|set\s*up)\s+(flask|express|fastapi|django)\b",
        r"\b(create|add|implement|set\s*up|build)\s+(a |an )?(login|auth\w*)\s*(system|page|flow)?\b",
    )
]


def _line_is_safe_exclusion(line: str) -> bool:
    return any(p.search(line) for p in _SAFE_PHRASE_PATTERNS)


def _line_is_blocked_instruction(line: str) -> bool:
    """
    True if the line contains an imperative "go build the forbidden thing" instruction
    that is NOT itself negated nearby (e.g. "Do not add a backend" must NOT count as
    a blocked instruction — it's the opposite, an exclusion).
    """
    for pattern in _BLOCKED_INSTRUCTION_PATTERNS:
        for m in pattern.finditer(line):
            before = line[max(0, m.start() - 15):m.start()]
            if _NEGATION_RE.search(before):
                continue
            return True
    return False


# Section headings under which mentioning a forbidden technology is normal — the
# whole point of these sections is to say what is NOT being built (or must NOT
# be done).  Two broad families:
#   - "out of scope" / "excluded" / "non-goals" → lists things that are out of scope
#   - "forbidden" / "prohibited" / "not allowed" → lists things that are PROHIBITED;
#     lines in these sections describe actions the builder must NOT take, so a line
#     like "Introducing any database connections" means "don't do this," not "do it."
_EXCLUSION_SECTION_RE = re.compile(
    r"out of scope|not included|excluded|non-?goals?|future work|explicitly excluded"
    r"|forbidden|prohibited|not allowed",
    re.IGNORECASE,
)


def _exclusion_section_flags(text: str) -> list[bool]:
    """
    Walk `text` line by line and return, for each line, whether it falls under a
    heading that marks an exclusion/out-of-scope section (e.g. "## Out of Scope (V1)").
    The flag stays active until the next heading of any kind.
    """
    flags = []
    in_exclusion = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_exclusion = bool(_EXCLUSION_SECTION_RE.search(stripped.lstrip("#").strip()))
        flags.append(in_exclusion)
    return flags


def detect_negative_constraints(*texts: str) -> dict:
    """
    Deterministic keyword scan across raw_input / mvp_scope / clean_requirements
    for explicit negative constraints (e.g. "frontend-only", "no backend").
    Returns a dict of constraint_name -> bool.
    """
    combined = "\n".join(t for t in texts if t).lower()
    constraints = {
        name: any(re.search(p, combined) for p in patterns)
        for name, patterns in _CONSTRAINT_DETECT_PATTERNS.items()
    }
    # Implication rules — a frontend-only app has none of the backend trappings.
    if constraints["frontend_only"]:
        for k in ("no_backend", "no_database", "no_api", "no_login", "no_auth"):
            constraints[k] = True
    if constraints["no_backend"]:
        constraints["no_api"] = True
    if constraints["no_login"]:
        constraints["no_auth"] = True
    return constraints


def _constraints_to_prompt_text(constraints: dict | None) -> str:
    """Render active constraints as hard directives to inject into a GPT prompt."""
    if not constraints:
        return ""
    active = [_CONSTRAINT_DIRECTIVES[k] for k, v in constraints.items() if v and k in _CONSTRAINT_DIRECTIVES]
    if not active:
        return ""
    lines = ["HARD CONSTRAINTS FROM THE REQUIREMENTS — DO NOT VIOLATE THESE UNDER ANY CIRCUMSTANCE:"]
    lines += [f"- {a}" for a in active]
    lines.append(
        "If the spec above mentions a Backend/API or Database section that conflicts with these "
        "constraints, ignore those parts of the spec — these constraints take precedence."
    )
    return "\n".join(lines)


def _term_pattern(term: str) -> str:
    """
    Most forbidden terms are matched as escaped literal substrings. Terms prefixed
    with "regex:" are matched as raw regex instead — used to disambiguate plain
    English words (e.g. "express") from the technology they coincidentally spell.
    """
    if term.startswith("regex:"):
        return term[len("regex:"):]
    return re.escape(term)


def _term_hits(text: str, term: str) -> list[str]:
    """
    Return lines where `term` appears in `text` and is not a safe exclusion.
    Classification order per line:
      1. Explicit "build the forbidden thing" instruction -> always a hit,
         even inside an Out of Scope / Excluded section.
      2. Line falls under an Out of Scope / Not Included / Excluded / Non-goals /
         Future Work heading -> never a hit (that's the point of the section).
      3. Whole-line safe-exclusion phrasing (e.g. "no backend is required",
         "excluded", "without a database") -> never a hit.
      4. Otherwise fall back to a local negation window around the match.
    """
    hits = []
    term_re = _term_pattern(term)
    section_flags = _exclusion_section_flags(text)
    for line, in_exclusion_section in zip(text.splitlines(), section_flags):
        if line.strip().startswith("#"):
            continue  # markdown heading — a required section title, not an instruction
        if not re.search(term_re, line, re.IGNORECASE):
            continue
        if _line_is_blocked_instruction(line):
            hits.append(line.strip())
            continue
        if in_exclusion_section:
            continue
        if _line_is_safe_exclusion(line):
            continue
        for m in re.finditer(term_re, line, re.IGNORECASE):
            before = line[max(0, m.start() - 40):m.start()]
            after = line[m.end():m.end() + 40]
            if _NEGATION_RE.search(before) or _NEGATION_RE.search(after):
                continue
            # "React Context API", "Browser API", "Web API", etc. are frontend
            # framework concepts, not backend API endpoints — never a violation.
            if _FRONTEND_API_MODIFIER_RE.search(before):
                continue
            hits.append(line.strip())
            break
    return hits


def check_requirements_consistency(
    constraints: dict,
    artifacts: dict,
) -> tuple[bool, str]:
    """
    Rule-based, deterministic check: do the generated planning artifacts violate
    any active negative constraint (e.g. "no backend") detected from the
    requirements? `artifacts` maps filename -> text, e.g.
    {"ARCHITECTURE.md": ..., "smoke_checks.md": ..., "build_prompt.txt": ...}.
    """
    lines = ["", "=" * 60, "  Requirements Consistency Check (rule-based)", "=" * 60]
    active = [k for k, v in constraints.items() if v and k in _CONSTRAINT_FORBIDDEN_TERMS]

    if not active:
        lines.append("  No negative constraints detected in requirements — nothing to check.")
        lines.append("=" * 60)
        return True, "\n".join(lines)

    lines.append(f"  Active constraints: {', '.join(active)}")
    lines.append("")

    violations = []
    for constraint in active:
        for term in _CONSTRAINT_FORBIDDEN_TERMS[constraint]:
            for fname, text in artifacts.items():
                for hit in _term_hits(text, term):
                    violations.append((constraint, term, fname, hit))

    if violations:
        for constraint, term, fname, hit in violations:
            display_term = term[len("regex:"):] if term.startswith("regex:") else term
            lines.append(f"[FAIL] {fname}: violates '{constraint}' — found '{display_term}' in: {hit}")
    else:
        lines.append("[PASS] No forbidden terms found for any active constraint")

    lines.append("")
    lines.append(f"  Violations found: {len(violations)}")
    lines.append("  RESULT: CONSISTENT WITH REQUIREMENTS" if not violations
                 else "  RESULT: REQUIREMENTS VIOLATED — artifacts must be regenerated/fixed before build")
    lines.append("=" * 60)
    return (len(violations) == 0), "\n".join(lines)


class RequirementsConsistencyError(RuntimeError):
    """Raised when generated planning artifacts violate explicit negative constraints."""


MVP_SCOPE_SYSTEM = """You are a pragmatic product manager. You are given a vague product idea
that is NOT fully specified. Define the smallest reasonable MVP scope — do not gold-plate,
do not add features beyond what is needed to make the idea usable.

Output exactly this format:

# MVP Scope: <short product name>

## Problem
One sentence.

## Target User
One sentence.

## MVP Scope Decision
2-4 sentences explaining what you are choosing to build first and why, and what you are
deliberately leaving out.

## In Scope (V1)
Numbered list of the smallest set of features that make this usable.

## Explicitly Out of Scope (V1)
Numbered list.
"""

def generate_mvp_scope(idea_text: str) -> str:
    return gpt([
        {"role": "system", "content": MVP_SCOPE_SYSTEM},
        {"role": "user", "content": f"Here is the product idea:\n\n{idea_text}"},
    ])


REQUIREMENTS_NORMALIZE_SYSTEM = """You are a precise technical writer. You are given requirements
that already exist (from a Jira ticket, written notes, or an MVP scope decision). Normalize them
into a clean requirements document.

Do NOT invent new features, scope, or product direction beyond what is stated or strongly implied.
Do NOT act as a product manager — only clarify and structure what already exists.

Output exactly this format:

# Clean Requirements: <product name from input>

## Source Summary
1-2 sentences describing what was provided.

## Requirements
Numbered list of every requirement found in the input, written clearly.

## Acceptance Criteria
Numbered list, taken directly from the input if present, otherwise write "(not specified)".

## Open Questions
List anything ambiguous or missing that an engineer would need to ask about. Do not guess answers.
"""

def normalize_requirements(source_text: str) -> str:
    return gpt([
        {"role": "system", "content": REQUIREMENTS_NORMALIZE_SYSTEM},
        {"role": "user", "content": f"Here are the existing requirements:\n\n{source_text}"},
    ])


ARCHITECTURE_SYSTEM = """You are a senior engineer writing an architecture contract for an MVP build.
Given an MVP spec, write ARCHITECTURE.md.

Output exactly this format with ALL section headers present, even if a section is short:

# Architecture

## Stack
Name the exact stack (language, framework, database, frontend framework). Be specific —
never say "a modern stack".

## File / Folder Boundaries
Describe the folder structure and which files own which responsibility
(e.g. "backend/app.py owns all API routes and DB access; frontend never accesses the DB directly").

## Forbidden Shortcuts
Bullet list of shortcuts that are NOT allowed for this build (e.g. localStorage instead of a
required database, mock data instead of real API calls, skipping a required endpoint).

## Smoke Checks
Bullet list of checks that must pass before this build is considered working
(install, build, API responds, DB has rows, etc).

## Deployment Assumptions
State explicitly: local only, macOS, no Docker, which ports are used, and the manual run command.
No cloud deployment is assumed.

If the requirements explicitly exclude something (e.g. "no backend", "frontend-only", "no database"),
the Stack, File/Folder Boundaries, and Deployment Assumptions sections must reflect that exclusion —
state plainly that there is no backend/database/etc, rather than inventing one anyway.
"""

def generate_architecture(spec: str, constraints: dict | None = None) -> str:
    constraint_text = _constraints_to_prompt_text(constraints)
    user_msg = f"Here is the MVP spec:\n\n{spec}"
    if constraint_text:
        user_msg += f"\n\n{constraint_text}"
    return gpt([
        {"role": "system", "content": ARCHITECTURE_SYSTEM},
        {"role": "user", "content": user_msg},
    ])


_ARCH_REQUIRED_SECTIONS = (
    "## Stack",
    "## File / Folder Boundaries",
    "## Forbidden Shortcuts",
    "## Smoke Checks",
    "## Deployment Assumptions",
)

_STACK_KEYWORDS = (
    "python", "flask", "fastapi", "django", "node", "express",
    "react", "vue", "next", "vite", "postgres", "postgresql",
    "sqlite", "mysql", "mongodb",
)

_DEPLOYMENT_KEYWORDS = ("local", "localhost", "macos", "port", "docker")


def _section_body(arch_text: str, header: str) -> str:
    """Return the text under a '## Header' until the next '## ' or end of doc."""
    pattern = re.escape(header) + r"\s*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, arch_text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def check_architecture_contract(arch_text: str) -> tuple[bool, str]:
    """
    Rule-based, deterministic check of the ARCHITECTURE.md planning artifact.
    Not a GPT validation step — pure keyword/structure rules.
    """
    lines = ["", "=" * 60, "  Architecture Contract Check (rule-based)", "=" * 60]
    passed, failed = 0, 0

    for header in _ARCH_REQUIRED_SECTIONS:
        if header.lower() in arch_text.lower():
            lines.append(f"[PASS] Section present: {header}")
            passed += 1
        else:
            lines.append(f"[FAIL] Missing required section: {header}")
            failed += 1

    stack_body = _section_body(arch_text, "## Stack")
    if stack_body and any(k in stack_body.lower() for k in _STACK_KEYWORDS):
        lines.append("[PASS] Stack is named with a concrete technology")
        passed += 1
    else:
        lines.append("[FAIL] Stack section does not name a concrete, recognizable technology")
        failed += 1

    boundaries_body = _section_body(arch_text, "## File / Folder Boundaries")
    if len(boundaries_body) > 40 and "/" in boundaries_body:
        lines.append("[PASS] File/folder boundaries are described with concrete paths")
        passed += 1
    else:
        lines.append("[FAIL] File/folder boundaries are missing or too vague (no file paths)")
        failed += 1

    shortcuts_body = _section_body(arch_text, "## Forbidden Shortcuts")
    if len(re.findall(r"^[-*]\s+\S", shortcuts_body, re.MULTILINE)) >= 1:
        lines.append("[PASS] Forbidden shortcuts are listed")
        passed += 1
    else:
        lines.append("[FAIL] No forbidden shortcuts listed")
        failed += 1

    smoke_body = _section_body(arch_text, "## Smoke Checks")
    if len(re.findall(r"^[-*]\s+\S", smoke_body, re.MULTILINE)) >= 1:
        lines.append("[PASS] Smoke checks are listed")
        passed += 1
    else:
        lines.append("[FAIL] No smoke checks listed")
        failed += 1

    deploy_body = _section_body(arch_text, "## Deployment Assumptions")
    if deploy_body and any(k in deploy_body.lower() for k in _DEPLOYMENT_KEYWORDS):
        lines.append("[PASS] Deployment assumptions are explicit")
        passed += 1
    else:
        lines.append("[FAIL] Deployment assumptions are missing or not explicit")
        failed += 1

    lines.append("")
    lines.append(f"  Contract checks: {passed} passed, {failed} failed")
    lines.append("  RESULT: CONTRACT OK" if failed == 0 else "  RESULT: CONTRACT VIOLATIONS — review ARCHITECTURE.md")
    lines.append("=" * 60)
    return failed == 0, "\n".join(lines)


def _strip_forbidden_lines(text: str, constraints: dict | None) -> str:
    """Deterministic filter — drops lines that mention terms forbidden by active constraints."""
    if not constraints or not text:
        return text
    active_terms = []
    for k, v in constraints.items():
        if v and k in _CONSTRAINT_FORBIDDEN_TERMS:
            active_terms.extend(_CONSTRAINT_FORBIDDEN_TERMS[k])
    if not active_terms:
        return text
    kept = []
    dropped_any = False
    for line in text.splitlines():
        if any(_term_hits(line, term) for term in active_terms):
            dropped_any = True
            continue
        kept.append(line)
    result = "\n".join(kept).strip()
    if dropped_any:
        # Deliberately avoids naming the excluded terms — naming them here would
        # itself trip the forbidden-term scan on this generated doc.
        result += "\n\n(Some content was removed because it conflicted with explicit requirements constraints.)"
    return result or "(none specified — excluded by requirements)"


_FRONTEND_ONLY_LIKE_KEYS = ("frontend_only", "no_backend", "no_database", "no_api")


def generate_smoke_checks_doc(spec: str, arch_text: str, constraints: dict | None = None) -> str:
    """Deterministic — extracts planned checks from the spec + architecture doc. No GPT call."""
    frontend_only_like = bool(constraints) and any(constraints.get(k) for k in _FRONTEND_ONLY_LIKE_KEYS)

    tech_proof = _strip_forbidden_lines(_section_body(spec, "## Technical Proof Requirements"), constraints)
    arch_smoke = _strip_forbidden_lines(_section_body(arch_text, "## Smoke Checks"), constraints)
    parts = ["# Smoke Checks (Plan)", ""]
    parts.append("## From MVP Spec — Technical Proof Requirements")
    parts.append(tech_proof or "(none specified)")
    parts.append("")
    parts.append("## From ARCHITECTURE.md — Smoke Checks")
    parts.append(arch_smoke or "(none specified)")
    parts.append("")

    if frontend_only_like:
        parts.append("## Frontend-Only Baseline Checks")
        parts.append("- npm install")
        parts.append("- npm run build")
        parts.append("- Manual check: interacting with the UI updates what is displayed in the browser")
        parts.append("")
        parts.append("## Executed By")
        parts.append("These are validated at build time by `smoke_checks/run_smoke.sh` "
                      "(install/build checks only — no backend or database is required for this app).")
    else:
        parts.append("## Executed By")
        parts.append("These are validated at build time by `smoke_checks/run_smoke.sh` "
                      "(install/build/API/DB checks) and the static architecture verification step.")
    return "\n".join(parts)


# ── Step 1: MVP Spec ───────────────────────────────────────────────────────────

SPEC_SYSTEM = """You are a senior product manager who writes precise MVP specifications.
Given any MVP idea, Jira ticket, or product notes, produce a clean spec that
an engineer can build from without asking follow-up questions.

Your spec must include ALL of these sections:

# MVP Spec: <short product name>

## Product Goal
One sentence. What problem does this solve and for whom?

## Target User
Who uses this and in what context?

## Key Features (MVP scope only)
Numbered list. Each feature must be specific enough to implement.

## Screens / UI
List each screen or view the user interacts with.

## Backend / API
List each API endpoint or background job needed.
If the spec requires a database, EVERY data operation must go through the API — never directly from the frontend.
If the requirements explicitly say this is frontend-only / has no backend / no API, write this section as
exactly: "No backend is required." Do not invent endpoints, routes, or background jobs.

## Database
List the key tables/collections and their main fields.
If the requirements explicitly say there is no database, write this section as exactly:
"No database is required." Do not invent tables, schemas, or queries.

## Acceptance Criteria
Numbered list. Observable pass/fail checks a tester can verify.

## Technical Proof Requirements
EXACT shell commands that must pass for this build to be considered complete.
These are non-negotiable. A build that passes visual checks but fails these is REJECTED.

Format:
- <command> → <expected result>

If a backend/API/database IS required, always include:
- A curl command for every API endpoint
- A database query confirming rows exist
- A grep check confirming localStorage is NOT used for persistence (if a database is required)

Example for a notes app with Flask + PostgreSQL:
- curl http://127.0.0.1:5001/notes → returns JSON array
- curl -X POST http://127.0.0.1:5001/notes -H 'Content-Type: application/json' -d '{"content":"test"}' → returns created note with id
- psql -d mvp_pipeline_db -c "SELECT * FROM notes;" → shows note rows
- grep -r "localStorage" frontend/src/ → returns EMPTY (localStorage forbidden for note persistence)

If the requirements explicitly say this is frontend-only / has no backend / no database / no API,
NEVER include curl, psql, or any backend/database command. Instead use only frontend-appropriate
checks, for example:
- npm run build → succeeds with no errors
- grep -r "fetch(\\|axios" frontend/src/ → returns EMPTY (confirms no backend API calls were added, none required)

## Out of Scope (V1)
What is explicitly NOT being built in this version?

Be direct. No marketing language. No filler. Engineers read this to build.
"""

def generate_mvp_spec(raw_input: str, constraints: dict | None = None) -> str:
    constraint_text = _constraints_to_prompt_text(constraints)
    user_msg = f"Here is the MVP idea:\n\n{raw_input}"
    if constraint_text:
        user_msg += f"\n\n{constraint_text}"
    return gpt([
        {"role": "system", "content": SPEC_SYSTEM},
        {"role": "user", "content": user_msg},
    ])


# ── Step 2: Claude Code Build Prompt ──────────────────────────────────────────

BUILD_PROMPT_SYSTEM = """You are a senior engineer who writes precise build instructions for Claude Code.
Given an MVP spec, write a Claude Code build prompt that produces a working local app.

Rules:
- Be extremely specific. Claude Code should not need to make architectural decisions.
- Specify the exact tech stack.
- Specify the exact folder structure.
- Specify each file to create and what it should contain.
- Specify how to run the app (exact commands).
- Specify exact port numbers.
- Specify any environment variables needed (as .env.example).
- Do NOT say "build a nice app" — say exactly what files, routes, components, and schemas to create.
- The output must be a local app that runs on macOS without Docker.
- Stay within MVP scope. Do not gold-plate.

CRITICAL ARCHITECTURE RULES — NON-NEGOTIABLE:
- If the spec requires a database, that database is the ONLY permitted persistence layer.
  localStorage, sessionStorage, in-memory arrays, and JSON files are FORBIDDEN as substitutes.
- Every API endpoint listed in the spec MUST be implemented. Skipping any endpoint makes the build INCOMPLETE.
- The frontend MUST call the backend API for all data operations. It must NEVER read or write data directly without going through the API.
- Do NOT take shortcuts that satisfy visual behavior but skip required architecture.
  Example of a forbidden shortcut: using localStorage to persist notes when PostgreSQL was required.
- Include the Technical Proof Requirements from the spec at the end of the prompt as a checklist.
  Claude Code must confirm each one passes before declaring the build complete.
- The reverse also applies: if the requirements explicitly EXCLUDE something (e.g. "frontend-only",
  "no backend", "no database", "no login"), the build prompt must NOT instruct Claude Code to build
  that excluded thing, even if the spec's template sections mention it. Excluding it correctly is
  part of the spec being followed correctly — it is not an omission.

Start with: "Build the following MVP locally:"
"""

def generate_build_prompt(spec: str, constraints: dict | None = None) -> str:
    constraint_text = _constraints_to_prompt_text(constraints)
    user_msg = f"Write the Claude Code build prompt for this MVP spec:\n\n{spec}"
    if constraint_text:
        user_msg += f"\n\n{constraint_text}"
    return gpt([
        {"role": "system", "content": BUILD_PROMPT_SYSTEM},
        {"role": "user", "content": user_msg},
    ])


# ── Step 2c: Sprint Decomposition ─────────────────────────────────────────────
# Breaks a large MVP into independently-buildable sprints so a huge product does
# not have to be built in one giant (credit-burning) Claude Code run.  This is a
# high-leverage architecture decision, so it deliberately uses GPT4O_MODEL (the
# stronger reasoning model already used for legal/governance review) instead of
# GPT_MODEL — every other planning step in this pipeline stays on GPT-mini.

SPRINT_PLAN_SYSTEM = """You are a senior staff engineer and technical architect. Your job is to break a \
large MVP into a sequence of sprints that are each independently buildable, demoable, and make \
product sense in isolation.

You are given the clean requirements, the MVP spec, and ARCHITECTURE.md for one MVP.

## STEP 1 — Assess product complexity

Before deciding sprint count, explicitly judge the complexity of THIS specific product by weighing:
- number of distinct screens/views
- number of distinct user workflows (not just screens — an end-to-end action a user completes)
- number of user roles (e.g. anonymous user vs admin vs reviewer)
- whether a backend/API is required
- whether a database/persistence layer is required
- whether auth/login is required
- AI/agent complexity (e.g. a single prompt call vs a multi-step agent/review loop)
- third-party integrations
- admin/dashboard surfaces
- deployment/infrastructure complexity
- test/review complexity
- whether the work naturally splits into pieces that are each independently demoable

Classify the product into one of: "simple", "moderate", "complex", "very_complex".

## STEP 2 — Choose a sprint count from this complexity (never default to a fixed number)

Use these as guidelines, not rigid brackets — pick the count that actually fits THIS product:
- Simple frontend-only toy app (e.g. a single-screen picker/utility, no backend, no roles): 2-3 sprints
- Normal frontend MVP (a few screens, one primary workflow, no backend): 3-5 sprints
- Frontend + backend + database MVP: 5-7 sprints
- AI/data/dashboard product (agent loops, data pipelines, analytics views): 6-9 sprints
- Complex product with auth/admin/integrations/deployment concerns: 8-12 sprints

Sprint count must always be an integer between 2 and 12 inclusive. Do not invent artificial splits \
to pad the count for a simple product, and do not compress a genuinely complex product (multiple \
workflows, roles, backend, persistence, integrations, deployment) down into just 2-3 sprints — under- \
splitting a complex product is just as wrong as over-splitting a simple one.

## STEP 3 — Design the sprints

### Sprint 1 — get this right, it is the most important sprint in the plan

Sprint 1 is what gets demoed first. It must be the smallest VISUALLY IMPRESSIVE product foundation —
not the smallest technically-first task.

- Sprint 1 must be runnable and visually demonstrable on its own: a real UI shell or a real core \
  flow that looks like the product, never random setup/scaffolding work with no visible behaviour.
- For anything beyond a trivially simple product (i.e. complexity_level is moderate, complex, or \
  very_complex), Sprint 1 should usually be a frontend shell / dashboard / mock-data slice: app \
  shell, main layout, navigation/sidebar/header, the primary list or dashboard populated with mock \
  data (cards or a table), and a visible stub for the one primary action (e.g. a button that opens \
  a modal). This gives a demo audience something that looks and feels like the real product on day \
  one, even before any backend, persistence, auth, or AI features exist.
- Sprint 1 should NOT be only a setup task, only a database/schema task, only a backend/API task, \
  or only a single plain form/CRUD screen — UNLESS the entire product is genuinely that simple \
  (complexity_level is "simple"). A bare data-entry form is too narrow to be a good Sprint 1 for \
  any moderate-or-larger product, even though it is "visually demonstrable" in the loosest sense — \
  it does not represent the product's shape the way a dashboard/shell slice does.
- Example — recruiting workspace product (complexity_level "complex", backend + auth + AI later): \
  a WEAK Sprint 1 is "Basic Candidate Entry Form" (too narrow, looks like a CRUD demo, not a \
  product). A GOOD Sprint 1 is "Recruiter Workspace Shell + Candidate Dashboard Mock" — goal: build \
  a polished frontend shell with navigation, a candidate dashboard using mock data, candidate \
  cards/table, and a visible "Add Candidate" action/modal stub. No persistence, auth, AI summaries, \
  or real backend yet — those become later sprints.
- Persistence, real backend/API wiring, auth, AI features, file upload, admin tooling, and audit \
  logging are USUALLY later sprints that plug into the Sprint 1 shell — not Sprint 1 itself — unless \
  the product cannot be visually demonstrated at all without one of them (e.g. an API-only product \
  with no UI).

Other sprint design rules:
- Each later sprint must build on top of previous sprints without requiring a rewrite of earlier work.
- Each sprint must be independently buildable by Claude Code in a single run without needing human \
  design decisions mid-build.
- Order sprints by product logic, not just technical convenience: foundation first, then the \
  primary user workflow, then secondary workflows, then roles/admin/dashboard, then persistence/ \
  integration/deployment/polish — but only where those stages are actually needed for THIS product.
- Never split one cohesive feature across two sprints in a way that leaves it half-working.
- Respect every negative constraint given to you (e.g. "no backend", "frontend-only", "no database", \
  "no login"). NEVER schedule a sprint that builds something the requirements explicitly exclude — \
  for a frontend-only product, do not invent backend, database, auth, or API sprints just to fill \
  out a sprint count.

## HARD RULE — if the requirements already define a sprint/phase/milestone plan

If the CLEAN REQUIREMENTS already contain an explicit sprint, phase, or milestone breakdown (e.g. \
"Sprint 1", "Phase 2", "Milestone 1"), you MUST preserve that plan exactly as given:
- Do NOT move or reshuffle features between sprints/phases/milestones.
- Do NOT change the sprint/phase/milestone numbers or their order.
- Do NOT combine or split sprints/phases/milestones the requirements already separated.
- Do NOT rename a sprint/phase/milestone in a way that changes its meaning or scope.
You ARE allowed to: clean up formatting, fill in missing structured fields (goal, \
files_modules_touched, user_visible_result, smoke_checks, dependencies, independently_demoable, \
build_now) for each existing sprint/phase/milestone, and add a note in "reason_for_sprint_count" \
that the plan was supplied by the requirements rather than decided by you. Only invent your own \
sprint decomposition from scratch when the requirements do NOT already define one.

Output STRICT JSON ONLY — no markdown fences, no prose before or after — matching exactly this shape:

{
  "product_name": "<short product name>",
  "complexity_level": "<simple | moderate | complex | very_complex>",
  "recommended_sprint_count": <integer 2-12>,
  "reason_for_sprint_count": "<1-3 sentences: the specific complexity factors that drove this count>",
  "total_sprints": <integer, must equal recommended_sprint_count and the number of sprints below>,
  "sprints": [
    {
      "number": 1,
      "title": "<short sprint title>",
      "goal": "<1-2 sentences: what this sprint accomplishes>",
      "why_this_order": "<1-2 sentences: why this sprint comes at this point in the sequence>",
      "files_modules_touched": ["<file or module path>", "..."],
      "user_visible_result": "<what a user/demo audience will actually see and be able to do>",
      "smoke_checks": ["<concrete check, e.g. 'npm run build succeeds'>", "..."],
      "dependencies": [],
      "independently_demoable": true,
      "build_now": true
    }
  ]
}

Field rules:
- "complexity_level" must be exactly one of: simple, moderate, complex, very_complex.
- "recommended_sprint_count" and "total_sprints" must match each other and must equal len(sprints).
- "reason_for_sprint_count" must name the actual factors from Step 1 that drove the count (e.g. \
  "Multiple workflows, backend persistence, dashboard views, AI review loop, and deployment \
  concerns."), not a generic restatement of the number.
- "dependencies" is a list of sprint numbers (integers) this sprint requires to already be built. \
  Sprint 1 must have an empty dependencies list.
- "independently_demoable" is true only if this sprint alone produces something a person could look \
  at and understand, even with no later sprints built.
- "build_now" is your own recommendation for which sprint should be built first — normally true only \
  for sprint 1, false for everything else, unless you have a strong specific reason otherwise. (The \
  pipeline will override this with the operator's actual --selected-sprint choice regardless.)
- Every field is required for every sprint. Do not omit fields.
"""


def detect_existing_sprint_plan(*texts: str) -> bool:
    """True if any of the given texts already define an explicit sprint/phase/milestone
    breakdown (e.g. "Sprint 1", "Phase 2", "Milestone 1") that the architect must preserve
    rather than reshuffle."""
    pattern = re.compile(r"\b(sprint|phase|milestone)\s+\d+\b", re.IGNORECASE)
    return any(pattern.search(t or "") for t in texts)


class SprintPlanParseError(RuntimeError):
    """Raised when the sprint architect's response cannot be parsed as a valid sprint plan."""


class SprintNotFoundError(ValueError):
    """Raised when --selected-sprint refers to a sprint number not present in the plan."""


def _extract_json_object(text: str) -> str:
    """Strip markdown code fences (```json ... ``` or ``` ... ```) if present, else
    fall back to slicing between the first '{' and the last '}'. Deterministic, no GPT call."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


_SPRINT_FIELD_DEFAULTS = {
    "title": "",
    "goal": "",
    "why_this_order": "",
    "files_modules_touched": [],
    "user_visible_result": "",
    "smoke_checks": [],
    "dependencies": [],
    "independently_demoable": False,
    "build_now": False,
}


def _coerce_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


_VALID_COMPLEXITY_LEVELS = ("simple", "moderate", "complex", "very_complex")
_MIN_SPRINT_COUNT = 2
_MAX_SPRINT_COUNT = 12


def normalize_sprint_plan(data: dict) -> dict:
    """
    Fill in missing fields with safe defaults, coerce loose types, and sort sprints by
    number. Deterministic — tolerates a slightly malformed model response so a flaky
    JSON shape doesn't crash the pipeline. No GPT call.
    """
    sprints_in = data.get("sprints") or []
    normalized = []
    for i, raw_sprint in enumerate(sprints_in):
        entry = dict(_SPRINT_FIELD_DEFAULTS)
        entry.update(raw_sprint or {})
        try:
            entry["number"] = int(raw_sprint.get("number", i + 1))
        except (TypeError, ValueError):
            entry["number"] = i + 1
        entry["files_modules_touched"] = _coerce_list(entry["files_modules_touched"])
        entry["smoke_checks"] = _coerce_list(entry["smoke_checks"])
        entry["dependencies"] = [int(d) for d in _coerce_list(entry["dependencies"])
                                  if str(d).strip().lstrip("-").isdigit()]
        entry["independently_demoable"] = bool(entry["independently_demoable"])
        entry["build_now"] = bool(entry["build_now"])
        normalized.append(entry)
    normalized.sort(key=lambda s: s["number"])

    complexity_level = str(data.get("complexity_level") or "").strip().lower()
    if complexity_level not in _VALID_COMPLEXITY_LEVELS:
        complexity_level = "moderate"

    try:
        recommended_sprint_count = int(data.get("recommended_sprint_count", len(normalized) or _MIN_SPRINT_COUNT))
    except (TypeError, ValueError):
        recommended_sprint_count = len(normalized) or _MIN_SPRINT_COUNT
    recommended_sprint_count = max(_MIN_SPRINT_COUNT, min(_MAX_SPRINT_COUNT, recommended_sprint_count))

    reason_for_sprint_count = str(data.get("reason_for_sprint_count") or "").strip()

    return {
        "product_name": data.get("product_name", ""),
        "complexity_level": complexity_level,
        "recommended_sprint_count": recommended_sprint_count,
        "reason_for_sprint_count": reason_for_sprint_count,
        "total_sprints": data.get("total_sprints", len(normalized)),
        "sprints": normalized,
    }


def apply_selected_sprint(sprint_plan_json: dict, selected_sprint_number: int) -> dict:
    """
    Deterministic — stamps which sprint was actually selected for this run onto the plan,
    and makes "build_now" authoritative: true only for the selected sprint, false for every
    other sprint. The architect's own per-sprint "build_now" guess is advisory only; the
    operator's actual --selected-sprint choice always wins, for both the persisted
    sprint_plan.json and any rendering derived from it. Returns a new dict — does not
    mutate the input. No GPT call.
    """
    new_plan = dict(sprint_plan_json)
    new_plan["sprints"] = [
        {**s, "build_now": (s.get("number") == selected_sprint_number)}
        for s in sprint_plan_json.get("sprints", [])
    ]
    new_plan["selected_sprint"] = selected_sprint_number
    return new_plan


def parse_sprint_plan_json(raw_text: str) -> dict:
    """Parse + normalize the sprint architect's raw response into a sprint plan dict.
    Deterministic. Raises SprintPlanParseError on malformed/empty output."""
    candidate = _extract_json_object(raw_text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise SprintPlanParseError(
            f"Could not parse sprint plan JSON: {e}\n\nRaw model output:\n{raw_text[:1500]}"
        )
    if not isinstance(data, dict) or not data.get("sprints"):
        raise SprintPlanParseError(
            f"Sprint plan JSON is missing a non-empty 'sprints' list.\n\n"
            f"Raw model output:\n{raw_text[:1500]}"
        )
    return normalize_sprint_plan(data)


def select_sprint(sprint_plan_json: dict, selected_sprint_number: int) -> dict:
    """Deterministic lookup — returns the sprint entry matching selected_sprint_number.
    Raises SprintNotFoundError if it doesn't exist in the plan."""
    for s in sprint_plan_json.get("sprints", []):
        if s.get("number") == selected_sprint_number:
            return s
    available = [s.get("number") for s in sprint_plan_json.get("sprints", [])]
    raise SprintNotFoundError(
        f"Sprint {selected_sprint_number} not found in sprint plan (available sprints: {available})"
    )


def render_sprint_plan_markdown(sprint_plan_json: dict) -> str:
    """Full human-readable sprint plan (sprint_plan.md). Deterministic, no GPT call."""
    sprints = sorted(sprint_plan_json.get("sprints", []), key=lambda s: s.get("number", 0))
    total = sprint_plan_json.get("total_sprints", len(sprints))
    product = sprint_plan_json.get("product_name", "")
    complexity = sprint_plan_json.get("complexity_level", "moderate")
    recommended = sprint_plan_json.get("recommended_sprint_count", total)
    reason = sprint_plan_json.get("reason_for_sprint_count", "")
    lines = [f"# Sprint Plan{f': {product}' if product else ''}", ""]
    lines.append(f"**Complexity level:** {complexity}")
    lines.append("")
    lines.append(f"**Recommended sprint count:** {recommended}")
    lines.append("")
    if reason:
        lines.append(f"**Reason for sprint count:** {reason}")
        lines.append("")
    lines.append(f"Total sprints: {total}")
    lines.append("")
    for s in sprints:
        n = s.get("number")
        lines.append(f"## Sprint {n} of {total}: {s.get('title', '')}")
        lines.append("")
        lines.append(f"**Goal:** {s.get('goal', '')}")
        lines.append("")
        lines.append(f"**Why this sprint comes at this point:** {s.get('why_this_order', '')}")
        lines.append("")
        lines.append("**Files / modules likely touched:**")
        for f in s.get("files_modules_touched") or ["(not specified)"]:
            lines.append(f"- {f}")
        lines.append("")
        lines.append(f"**User-visible result:** {s.get('user_visible_result', '')}")
        lines.append("")
        lines.append("**Smoke checks for this sprint:**")
        for c in s.get("smoke_checks") or ["(not specified)"]:
            lines.append(f"- {c}")
        lines.append("")
        deps = s.get("dependencies") or []
        lines.append(f"**Dependencies:** {', '.join('Sprint ' + str(d) for d in deps) if deps else 'None'}")
        lines.append("")
        lines.append(f"**Independently demoable:** {'Yes' if s.get('independently_demoable') else 'No'}")
        lines.append("")
        lines.append(f"**Build now:** {'Yes' if s.get('build_now') else 'No'}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_sprint_plan_terminal(sprint_plan_json: dict, selected_sprint_number: int) -> str:
    """
    Concise terminal/live-log rendering of the sprint plan. "Build now" reflects the
    ACTUAL --selected-sprint choice (not the architect's own build_now opinion), since
    that choice — not the model's guess — is what the pipeline will act on.
    Deterministic, no GPT call.
    """
    sprints = sorted(sprint_plan_json.get("sprints", []), key=lambda s: s.get("number", 0))
    total = sprint_plan_json.get("total_sprints", len(sprints))
    complexity = sprint_plan_json.get("complexity_level", "moderate")
    recommended = sprint_plan_json.get("recommended_sprint_count", total)
    reason = sprint_plan_json.get("reason_for_sprint_count", "")
    lines = ["Architecture Sprint Plan", ""]
    lines.append(f"Complexity: {complexity}")
    lines.append(f"Recommended sprint count: {recommended}")
    if reason:
        lines.append(f"Reason: {reason}")
    lines.append("")
    for s in sprints:
        n = s.get("number")
        is_selected = (n == selected_sprint_number)
        lines.append(f"Sprint {n} of {total}: {s.get('title', '')}")
        lines.append(f"Goal: {s.get('goal', '')}")
        if is_selected:
            why_label = "Why first" if n == 1 else "Why now"
            lines.append(f"{why_label}: {s.get('why_this_order', '')}")
            lines.append(f"Output: {s.get('user_visible_result', '')}")
        lines.append(f"Build now: {'yes' if is_selected else 'no'}")
        lines.append("")
    lines.append(f"Selected Sprint: Sprint {selected_sprint_number} of {total}")
    lines.append("Claude Code will build only this sprint.")
    return "\n".join(lines)


def generate_sprint_plan(
    clean_requirements: str,
    mvp_spec: str,
    architecture_text: str,
    constraints: dict | None,
    run_dir,
    selected_sprint_number: int | None = None,
) -> tuple[dict, str]:
    """
    Smart, complexity-aware sprint decomposition — breaks a large MVP into product-sense,
    independently buildable sprints, choosing a sprint count (2-12) that actually fits the
    product's complexity instead of defaulting to a fixed number. Uses GPT4O_MODEL (see
    module note above on model choice).

    `selected_sprint_number` is optional and purely informational/normalizing: if given, it
    is stamped onto the persisted plan as "selected_sprint" and used to make "build_now"
    authoritative across all sprints (see apply_selected_sprint). It is NOT required — the
    caller may omit it and call apply_selected_sprint()/select_sprint() separately.

    Writes sprint_plan.json and sprint_plan.md directly into `run_dir` (a Path to the
    run folder) and returns (sprint_plan_json, sprint_plan_md).
    """
    constraint_text = _constraints_to_prompt_text(constraints)
    user_msg = (
        f"## CLEAN REQUIREMENTS\n{clean_requirements}\n\n"
        f"## MVP SPEC\n{mvp_spec}\n\n"
        f"## ARCHITECTURE.md\n{architecture_text}\n\n"
    )
    if constraint_text:
        user_msg += f"{constraint_text}\n\n"
    if detect_existing_sprint_plan(clean_requirements, mvp_spec):
        user_msg += (
            "## NOTE: existing sprint/phase/milestone plan detected\n"
            "The requirements above already define their own sprint/phase/milestone breakdown. "
            "Per your HARD RULE, preserve that plan exactly — same numbers, same order, same "
            "scope per sprint. Normalize it into the required JSON schema; do not redesign it.\n\n"
        )
    user_msg += (
        "Now produce the sprint decomposition. Output STRICT JSON ONLY, matching the schema "
        "described in your instructions — no markdown, no commentary, no code fences."
    )

    raw = gpt4o([
        {"role": "system", "content": SPRINT_PLAN_SYSTEM},
        {"role": "user", "content": user_msg},
    ])
    sprint_plan_json = parse_sprint_plan_json(raw)
    if selected_sprint_number is not None:
        sprint_plan_json = apply_selected_sprint(sprint_plan_json, selected_sprint_number)
    sprint_plan_md = render_sprint_plan_markdown(sprint_plan_json)

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "sprint_plan.json").write_text(json.dumps(sprint_plan_json, indent=2), encoding="utf-8")
    (run_dir / "sprint_plan.md").write_text(sprint_plan_md, encoding="utf-8")

    return sprint_plan_json, sprint_plan_md


def render_selected_sprint_scope_markdown(selected_sprint: dict, sprint_plan_json: dict) -> str:
    """Human-readable scope doc for the selected sprint (selected_sprint_scope.md)."""
    total = sprint_plan_json.get("total_sprints", len(sprint_plan_json.get("sprints", [])))
    n = selected_sprint.get("number")
    lines = [
        f"# Selected Sprint Scope — Sprint {n} of {total}",
        "",
        f"## Title\n{selected_sprint.get('title', '')}",
        "",
        f"## Goal\n{selected_sprint.get('goal', '')}",
        "",
        f"## Why This Sprint Comes At This Point\n{selected_sprint.get('why_this_order', '')}",
        "",
        "## Files / Modules Likely Touched",
    ]
    for f in selected_sprint.get("files_modules_touched") or ["(not specified)"]:
        lines.append(f"- {f}")
    lines += ["", f"## User-Visible Result\n{selected_sprint.get('user_visible_result', '')}",
              "", "## Smoke Checks For This Sprint"]
    for c in selected_sprint.get("smoke_checks") or ["(not specified)"]:
        lines.append(f"- {c}")
    deps = selected_sprint.get("dependencies") or []
    lines += ["", "## Dependencies",
              ", ".join(f"Sprint {d}" for d in deps) if deps else "None"]
    lines += ["", "## Independently Demoable",
              "Yes" if selected_sprint.get("independently_demoable") else "No"]
    lines += ["", "## Selected For Build Now",
              "Yes — this sprint was selected via --selected-sprint and will be sent to Claude Code."]
    return "\n".join(lines) + "\n"


def generate_selected_sprint_build_prompt(
    clean_requirements: str,
    mvp_spec: str,
    architecture_text: str,
    sprint_plan_json: dict,
    selected_sprint: dict,
    constraints: dict | None,
    run_dir,
) -> str:
    """
    Deterministic template — NO GPT call. This is what Claude Code actually receives
    in sprint mode: it WRAPS/REPLACES the normal full-MVP build_prompt.txt so Claude
    Code never sees the full multi-sprint build in one shot.

    Writes selected_sprint_scope.md, selected_sprint_build_prompt.txt, and the numbered
    sprint_{N}_scope.md / sprint_{N}_build_prompt.txt copies into `run_dir`.
    """
    total = sprint_plan_json.get("total_sprints", len(sprint_plan_json.get("sprints", [])))
    num = selected_sprint.get("number")
    title = selected_sprint.get("title") or f"Sprint {num}"
    all_sprints = sorted(sprint_plan_json.get("sprints", []), key=lambda s: s.get("number", 0))
    future_sprints = [s for s in all_sprints if s.get("number", 0) > num]
    earlier_sprints = [s for s in all_sprints if s.get("number", 0) < num]

    parts = [
        f"Build the following MVP locally — SPRINT {num} OF {total} ONLY: {title}",
        "",
        f"Build only Sprint {num}.",
        f"Do not build Sprint {num + 1} or any sprint after it. Do not build any sprint other "
        f"than Sprint {num}.",
        "Future sprints are listed later in this prompt for context ONLY — they are NOT "
        "instructions to build anything now.",
        "",
        f"## Sprint {num} Goal",
        selected_sprint.get("goal", ""),
        "",
        f"## Sprint {num} — User-Visible Result (what must be demoable when this sprint is done)",
        selected_sprint.get("user_visible_result", ""),
        "",
        f"## Sprint {num} — Files / Modules Likely Touched",
    ]
    for f in selected_sprint.get("files_modules_touched") or [
        "(not specified — use judgment within the architecture below)"
    ]:
        parts.append(f"- {f}")
    parts += ["", f"## Sprint {num} — Smoke Checks For This Sprint"]
    for c in selected_sprint.get("smoke_checks") or ["(not specified)"]:
        parts.append(f"- {c}")

    if earlier_sprints:
        parts += ["", "## Earlier Sprints (assume already built)"]
        for s in earlier_sprints:
            parts.append(
                f"- Sprint {s.get('number')}: {s.get('title', '')} — assume this already exists; "
                "do not rebuild it, but you may use/extend it."
            )

    parts += [
        "",
        "## Full MVP Spec (context only — do not build beyond the selected sprint above)",
        mvp_spec,
        "",
        "## ARCHITECTURE.md (context only)",
        architecture_text,
    ]

    if future_sprints:
        parts += ["", "## Future Sprints — REFERENCE ONLY, DO NOT BUILD"]
        for s in future_sprints:
            parts.append(
                f"- Sprint {s.get('number')}: {s.get('title', '')} — {s.get('goal', '')} "
                f"(NOT in scope for this build. Do not implement Sprint {s.get('number')} now.)"
            )

    constraint_text = _constraints_to_prompt_text(constraints)
    if constraint_text:
        parts += ["", constraint_text]

    parts += [
        "",
        "## Hard Rules For This Build",
        f"- Build ONLY Sprint {num}: {title}. Nothing more, nothing less.",
        "- Do not implement any feature that belongs to a later sprint listed above.",
        "- Leave clean, obviously-named extension points (placeholder components, routes, or "
        "functions, with no half-finished logic) so future sprints can be added later without "
        "a rewrite.",
        "- Do not add a backend, database, login, or API unless this sprint's scope explicitly "
        "requires it and the constraints above do not forbid it.",
        "- Keep the scope small enough to build and demo in one sitting.",
        "- The result must be runnable and visually demonstrable end-to-end for this sprint alone.",
        "- Be concrete: write the exact files/components/routes this sprint needs per the "
        "architecture above. Do not say \"build a nice app.\"",
        "",
        "Save all files into the current directory. Create a complete, runnable local app for "
        "this sprint's scope only. After building, print a summary of what was created.",
    ]

    prompt_text = "\n".join(parts)

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    scope_md = render_selected_sprint_scope_markdown(selected_sprint, sprint_plan_json)
    (run_dir / "selected_sprint_scope.md").write_text(scope_md, encoding="utf-8")
    (run_dir / "selected_sprint_build_prompt.txt").write_text(prompt_text, encoding="utf-8")
    (run_dir / f"sprint_{num}_scope.md").write_text(scope_md, encoding="utf-8")
    (run_dir / f"sprint_{num}_build_prompt.txt").write_text(prompt_text, encoding="utf-8")

    return prompt_text


# ═════════════════════════════════════════════════════════════════════════════
# Existing App Upgrade Mode
# ═════════════════════════════════════════════════════════════════════════════
# A second, additive entry point alongside the normal "idea -> new MVP" pipeline.
# Given an existing local app + a feature request, this mode inspects the app,
# normalizes the requested features, finds the gap between them, and plans/builds
# ONLY the new feature work as numbered "feature sprints" on top of an immutable
# Sprint 0 baseline. It never regenerates the app from scratch and never says so.

_SCAN_IGNORE_DIRS = {
    "node_modules", "__pycache__", "venv", ".venv", ".git", "dist", "build",
    ".next", ".turbo", "coverage", ".pytest_cache", ".mypy_cache", "vendor",
    "target", ".idea", ".vscode",
}
_SCAN_MAX_FILES = 4000


def _safe_read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_read_text(path: Path, max_chars: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def scan_existing_app(existing_app_path: Path) -> dict:
    """
    Deterministic static repo scanner — no GPT call. Walks the existing app
    (skipping node_modules/.git/etc) and reports tech stack, package manager,
    frameworks, folder structure, entry points, and likely routes/components/
    API/data files. A robust simple scan is enough for v1 — this is not meant
    to be a perfect AST-level analysis.
    """
    existing_app_path = Path(existing_app_path).resolve()
    all_files: list[Path] = []
    top_level_dirs: list[str] = []

    for root, dirs, files in os.walk(existing_app_path):
        dirs[:] = [d for d in sorted(dirs) if d not in _SCAN_IGNORE_DIRS and not d.startswith(".")]
        root_path = Path(root)
        if root_path == existing_app_path:
            top_level_dirs = list(dirs)
        for fname in files:
            if len(all_files) >= _SCAN_MAX_FILES:
                break
            all_files.append(root_path / fname)
        if len(all_files) >= _SCAN_MAX_FILES:
            break

    rel_files = [str(f.relative_to(existing_app_path)) for f in all_files]

    def _find(*candidates: str) -> Path | None:
        for c in candidates:
            p = existing_app_path / c
            if p.exists():
                return p
        return None

    package_json_path = _find("package.json", "frontend/package.json")
    package_json = _safe_read_json(package_json_path) if package_json_path else {}
    deps = {**package_json.get("dependencies", {}), **package_json.get("devDependencies", {})}

    requirements_txt_path = _find("requirements.txt", "backend/requirements.txt")
    pyproject_path = _find("pyproject.toml", "backend/pyproject.toml")
    py_deps_text = (_safe_read_text(requirements_txt_path) if requirements_txt_path else "") + \
                   (_safe_read_text(pyproject_path) if pyproject_path else "")

    tech_stack: list[str] = []
    frontend_framework = None
    backend_framework = None
    package_manager = None

    if package_json_path:
        package_manager = "npm"
        if _find("pnpm-lock.yaml"):
            package_manager = "pnpm"
        elif _find("yarn.lock"):
            package_manager = "yarn"
        if "next" in deps:
            frontend_framework = "Next.js"
        elif "vite" in deps and "react" in deps:
            frontend_framework = "React + Vite"
        elif "react-scripts" in deps:
            frontend_framework = "React (Create React App)"
        elif "react" in deps:
            frontend_framework = "React"
        elif "vue" in deps:
            frontend_framework = "Vue"
        if "express" in deps:
            backend_framework = "Express / Node"
        tech_stack.append("Node.js")
        if frontend_framework:
            tech_stack.append(frontend_framework)

    if requirements_txt_path or pyproject_path:
        package_manager = package_manager or "pip"
        if re.search(r"\bflask\b", py_deps_text, re.IGNORECASE):
            backend_framework = backend_framework or "Flask"
        elif re.search(r"\bfastapi\b", py_deps_text, re.IGNORECASE):
            backend_framework = backend_framework or "FastAPI"
        elif re.search(r"\bdjango\b", py_deps_text, re.IGNORECASE):
            backend_framework = backend_framework or "Django"
        tech_stack.append("Python")
        if backend_framework:
            tech_stack.append(backend_framework)

    if not tech_stack:
        tech_stack.append("Unknown / undetected")

    database = None
    auth = None
    combined_dep_text = " ".join(deps.keys()) + " " + py_deps_text
    if re.search(r"sqlite", combined_dep_text, re.IGNORECASE) or any(f.endswith(".db") for f in rel_files):
        database = "SQLite"
    elif re.search(r"\bpg\b|postgres|psycopg", combined_dep_text, re.IGNORECASE):
        database = "PostgreSQL"
    elif re.search(r"mongoose|mongodb|pymongo", combined_dep_text, re.IGNORECASE):
        database = "MongoDB"
    elif re.search(r"sequelize|prisma|sqlalchemy", combined_dep_text, re.IGNORECASE):
        database = "SQL (ORM detected)"
    if re.search(r"passport|next-auth|firebase|jsonwebtoken|jwt|flask-login|flask-jwt", combined_dep_text, re.IGNORECASE):
        auth = "Auth library detected (see dependencies)"

    entry_points = [f for f in rel_files if Path(f).name in (
        "app.py", "main.py", "server.js", "index.js", "index.ts",
        "main.jsx", "main.tsx", "App.jsx", "App.tsx",
    )]

    def _list_dir_files(*dirnames: str, limit: int = 40) -> list[str]:
        out = []
        for d in dirnames:
            base = existing_app_path / d
            if base.exists():
                for f in rel_files:
                    if f.startswith(d.rstrip("/") + "/") or f.startswith(d.rstrip("/") + os.sep):
                        out.append(f)
                        if len(out) >= limit:
                            return out
        return out

    routes_pages = _list_dir_files("src/pages", "frontend/src/pages", "pages", "app")
    components = _list_dir_files("src/components", "frontend/src/components", "components")
    api_files = _list_dir_files("src/api", "frontend/src/api", "api", "routes", "backend/routes")
    data_files = _list_dir_files("src/data", "frontend/src/data", "data", "mock", "mocks")

    scripts = package_json.get("scripts", {}) if package_json else {}

    risky_files = [f for f in rel_files if Path(f).name in (
        ".env", ".env.local", "secrets.json", "credentials.json",
    ) or "migration" in f.lower() or "schema" in f.lower()]

    return {
        "root": str(existing_app_path),
        "tech_stack": tech_stack,
        "package_manager": package_manager,
        "frontend_framework": frontend_framework,
        "backend_framework": backend_framework,
        "database": database,
        "auth": auth,
        "top_level_dirs": top_level_dirs,
        "file_count": len(rel_files),
        "entry_points": entry_points,
        "routes_pages": routes_pages,
        "components": components,
        "api_files": api_files,
        "data_files": data_files,
        "scripts": scripts,
        "risky_files": risky_files[:30],
        "package_json_path": str(package_json_path.relative_to(existing_app_path)) if package_json_path else None,
        "requirements_txt_path": str(requirements_txt_path.relative_to(existing_app_path)) if requirements_txt_path else None,
    }


def write_existing_app_inventory(scan: dict, run_dir) -> str:
    """Deterministic — renders the scan dict into existing_app_inventory.md. No GPT call."""
    lines = ["# Existing App Inventory", "", f"**Root:** `{scan['root']}`", ""]
    lines.append(f"**Detected tech stack:** {', '.join(scan['tech_stack'])}")
    lines.append(f"**Package manager:** {scan['package_manager'] or 'Not detected'}")
    lines.append(f"**Frontend framework:** {scan['frontend_framework'] or 'None detected'}")
    lines.append(f"**Backend framework:** {scan['backend_framework'] or 'None detected'}")
    lines.append(f"**Database:** {scan['database'] or 'None detected'}")
    lines.append(f"**Auth:** {scan['auth'] or 'None detected'}")
    lines.append("")
    lines.append(f"**Total files scanned:** {scan['file_count']}")
    lines.append("")
    lines.append("## Folder Structure (top level)")
    for d in scan["top_level_dirs"] or ["(no subfolders)"]:
        lines.append(f"- {d}/")
    lines.append("")
    lines.append("## App Entry Points")
    for f in scan["entry_points"] or ["(none detected)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Routes / Pages (if detectable)")
    for f in scan["routes_pages"] or ["(none detected)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Components (if detectable)")
    for f in scan["components"] or ["(none detected)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## API Files (if detectable)")
    for f in scan["api_files"] or ["(none detected)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Data / Mock Files (if detectable)")
    for f in scan["data_files"] or ["(none detected)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Test / Build Scripts")
    if scan["scripts"]:
        for k, v in scan["scripts"].items():
            lines.append(f"- `{k}`: `{v}`")
    else:
        lines.append("(none detected — no package.json scripts found)")
    lines.append("")
    lines.append("## Important Files")
    for f in [scan["package_json_path"], scan["requirements_txt_path"]]:
        if f:
            lines.append(f"- {f}")
    lines.append("")
    lines.append("## Risky / Important Files")
    for f in scan["risky_files"] or ["(none flagged)"]:
        lines.append(f"- {f}")
    lines.append("")
    content = "\n".join(lines) + "\n"

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "existing_app_inventory.md").write_text(content, encoding="utf-8")
    return content


def run_baseline_health_check(existing_app_path: Path, scan: dict, run_dir) -> str:
    """
    Best-effort, SAFE health check — no installs, no servers started, no network calls.
    Only does cheap static checks: Python syntax compile on a sample of .py files, and
    `node --check` on a JS/TS entry point if node is on PATH. Reports HEALTHY / BROKEN /
    UNKNOWN. Deterministic except for the two optional subprocess checks, which are
    wrapped so a missing toolchain never crashes the pipeline.
    """
    existing_app_path = Path(existing_app_path)
    findings: list[str] = []
    broken = False
    checked_anything = False

    py_files = list(existing_app_path.rglob("*.py"))
    py_files = [p for p in py_files if not any(part in _SCAN_IGNORE_DIRS for part in p.parts)][:60]
    py_errors = []
    for p in py_files:
        checked_anything = True
        try:
            compile(p.read_text(encoding="utf-8", errors="ignore"), str(p), "exec")
        except SyntaxError as e:
            py_errors.append(f"{p.relative_to(existing_app_path)}: {e}")
    if py_errors:
        broken = True
        findings.append(f"Python syntax errors found in {len(py_errors)} file(s):")
        findings.extend(f"  - {e}" for e in py_errors[:10])
    elif py_files:
        findings.append(f"Python syntax check: {len(py_files)} file(s) compiled cleanly.")

    js_entry = None
    for cand in ("index.js", "main.jsx", "main.tsx", "App.jsx", "App.tsx", "server.js"):
        for ep in scan.get("entry_points", []):
            if Path(ep).name == cand:
                js_entry = existing_app_path / ep
                break
        if js_entry:
            break
    if js_entry and js_entry.suffix == ".js":
        checked_anything = True
        try:
            result = subprocess.run(
                ["node", "--check", str(js_entry)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                broken = True
                findings.append(f"`node --check {js_entry.name}` failed:\n{result.stderr.strip()[:500]}")
            else:
                findings.append(f"`node --check {js_entry.name}` passed.")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            findings.append("node not available on PATH — skipped JS syntax check.")

    install_cmd = None
    build_cmd = None
    dev_cmd = None
    test_cmd = None
    scripts = scan.get("scripts") or {}
    if scan.get("package_manager"):
        pm = scan["package_manager"]
        install_cmd = f"{pm} install"
        if "build" in scripts:
            build_cmd = f"{pm} run build"
        if "dev" in scripts or "start" in scripts:
            dev_cmd = f"{pm} run {'dev' if 'dev' in scripts else 'start'}"
        if "test" in scripts:
            test_cmd = f"{pm} run test"
    elif scan.get("requirements_txt_path"):
        install_cmd = "pip install -r requirements.txt"
        dev_cmd = "python app.py" if "app.py" in [Path(e).name for e in scan.get("entry_points", [])] else None

    if broken:
        status = "BROKEN"
    elif checked_anything:
        status = "HEALTHY"
    else:
        status = "UNKNOWN"
        findings.append("No safe static checks were applicable (no .py files, no recognizable JS entry point).")

    lines = ["# Baseline Health Check", "", f"**Status:** {status}", ""]
    lines.append("## Commands Detected")
    lines.append(f"- Install: `{install_cmd}`" if install_cmd else "- Install: (not detected)")
    lines.append(f"- Build: `{build_cmd}`" if build_cmd else "- Build: (not detected)")
    lines.append(f"- Dev: `{dev_cmd}`" if dev_cmd else "- Dev: (not detected)")
    lines.append(f"- Test: `{test_cmd}`" if test_cmd else "- Test: (not detected)")
    lines.append("")
    lines.append("## Findings")
    for f in findings or ["(no findings)"]:
        lines.append(f"- {f}")
    lines.append("")
    if status == "BROKEN":
        lines.append("## ⚠️ WARNING")
        lines.append(
            "The existing app appears to have pre-existing errors. Feature work will be planned "
            "and built on top of this baseline anyway, but regression checks below will have "
            "lower confidence, and any pre-existing breakage is NOT caused by this upgrade run."
        )
    elif status == "UNKNOWN":
        lines.append("## Note")
        lines.append(
            "Health could not be determined with safe static checks alone. Continuing, but "
            "regression-check confidence for this run is limited."
        )
    content = "\n".join(lines) + "\n"

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "baseline_health_check.md").write_text(content, encoding="utf-8")
    return content


EXISTING_APP_SUMMARY_SYSTEM = """You are a senior engineer onboarding onto an existing codebase you did \
not write. You are given a static repo scan and a baseline health check. Write a clear, honest, \
human-readable summary of what this app appears to do and how it is built.

Write in this exact format:

# Existing App Summary

## What This App Appears To Do
2-4 sentences, in plain product terms.

## Current Stack
Bullet list of frontend/backend/database/auth as detected.

## Current Features
Bullet list of the features/screens/flows you can infer exist.

## Data / Storage / Auth / API
What persistence, authentication, or API surface (if any) appears to exist today.

## What Is Missing Or Uncertain
Bullet list — be honest about what the static scan could not determine.

## What Should Be Preserved
Bullet list of behaviors, files, or conventions that future feature work must not break.

Do not invent features that are not evidenced by the inventory. Do not suggest rewriting anything. \
Never say this is a "new MVP" — this is an existing app being inspected, not created.
"""


def generate_existing_app_summary(inventory_md: str, health_md: str, run_dir) -> str:
    summary = gpt([
        {"role": "system", "content": EXISTING_APP_SUMMARY_SYSTEM},
        {"role": "user", "content": (
            f"## EXISTING APP INVENTORY\n{inventory_md}\n\n"
            f"## BASELINE HEALTH CHECK\n{health_md}\n\n"
            "Write the existing app summary."
        )},
    ])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "existing_app_summary.md").write_text(summary, encoding="utf-8")
    return summary


FEATURE_REQUIREMENTS_SYSTEM = """You are a product analyst normalizing a feature request against an \
existing application. You are given the existing app summary and the raw feature request text.

Write in this exact format:

# New Feature Requirements

## Requested Features
Numbered list — one clear feature per item, in the requester's own intent (not your redesign of it).

## Constraints From User
Bullet list of any explicit constraints stated in the request (e.g. "no new dependencies", \
"keep it free-tier"). If none stated, write "None stated."

## Explicit Non-Goals
Bullet list of things the request explicitly says NOT to do, or that are clearly out of scope.

## Assumptions
Bullet list of reasonable assumptions you had to make to fill gaps in the request.

## Ambiguities
Bullet list of anything genuinely unclear that a human should confirm before or during the sprint.

## Dependency Notes
Bullet list of which requested features depend on others (e.g. "AI reminders" depends on \
"persisted events" existing first).

This is feature work to ADD to the existing app above — never describe building a new app from \
scratch, and never restate the whole existing app as if it needs to be rebuilt.
"""


def generate_new_feature_requirements(feature_request_text: str, existing_app_summary: str, run_dir) -> str:
    requirements = gpt([
        {"role": "system", "content": FEATURE_REQUIREMENTS_SYSTEM},
        {"role": "user", "content": (
            f"## EXISTING APP SUMMARY\n{existing_app_summary}\n\n"
            f"## RAW FEATURE REQUEST\n{feature_request_text}\n\n"
            "Write the normalized feature requirements."
        )},
    ])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "new_feature_requirements.md").write_text(requirements, encoding="utf-8")
    return requirements


GAP_ANALYSIS_SYSTEM = """You are a staff engineer performing a change-impact / gap analysis before any \
code is written. You are given the existing app summary and the normalized new feature requirements.

Write in this exact format:

# Change Gap Analysis

## What Already Exists
Bullet list of existing capabilities that the requested features can build on.

## What Is Missing
Bullet list of capabilities that do not exist yet and must be added.

## Files / Areas Likely Impacted
Bullet list of specific files, folders, or modules (from the inventory) likely to be touched.

## Risks
Bullet list of concrete risks (breaking existing behavior, data migration, scope creep, etc).

## Feature Dependencies
Bullet list — which requested features must come before others.

## Migration Concerns
Bullet list — note any backend/database/auth migration implications. Write "None" if not applicable.

## Classification
One line: state whether this overall change is ADDITIVE, INVASIVE, or RISKY, with a one-sentence \
justification. ADDITIVE = mostly new files/routes/components with minimal touch to existing code. \
INVASIVE = requires modifying core existing files/behavior. RISKY = touches auth, data integrity, \
or has a high chance of breaking existing functionality.

Be specific and grounded in the actual existing app summary — do not write generic boilerplate.
"""


def generate_change_gap_analysis(existing_app_summary: str, new_feature_requirements: str,
                                  inventory_md: str, run_dir) -> str:
    gap = gpt([
        {"role": "system", "content": GAP_ANALYSIS_SYSTEM},
        {"role": "user", "content": (
            f"## EXISTING APP SUMMARY\n{existing_app_summary}\n\n"
            f"## NEW FEATURE REQUIREMENTS\n{new_feature_requirements}\n\n"
            f"## EXISTING APP INVENTORY\n{inventory_md}\n\n"
            "Write the change gap analysis."
        )},
    ])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "change_gap_analysis.md").write_text(gap, encoding="utf-8")
    return gap


ADDITIVE_ARCHITECTURE_SYSTEM = """You are a staff architect designing how to ADD features to an \
existing application safely. You are given the existing app summary, the new feature requirements, \
and the change gap analysis.

HARD RULES:
- Do not rewrite the app unless the gap analysis explicitly classifies the change as requiring it.
- Preserve the existing stack and conventions — do not introduce a different framework/language.
- Prefer adding new files/components/routes over modifying existing ones.
- Identify clean extension points in the existing structure.
- Keep every sprint runnable — no sprint should leave the app in a broken state.

Write in this exact format:

# Additive Architecture

## Extension Points
Bullet list of specific places in the existing app where new code should be hooked in (e.g. "add a \
new route module imported into the existing router", "add a new top-level nav item").

## Files Likely To Be Created
Bullet list of new files/folders this work will likely introduce.

## Files Likely To Be Modified
Bullet list of existing files that will likely need small, additive edits.

## Files That Should NOT Be Touched
Bullet list of existing files/areas that must be left alone (core app shell, unrelated features, \
existing styling conventions, etc).

## Migration Path
If backend, database, or auth is involved, describe the additive migration path (e.g. "add new \
tables; do not alter existing tables without a migration script"). Write "Not applicable" if none \
of those are involved.

## Sprint-Readiness Notes
1-3 sentences on how this architecture supports splitting the feature work into small, independently \
runnable sprints.
"""


def generate_additive_architecture(existing_app_summary: str, new_feature_requirements: str,
                                    gap_analysis: str, run_dir) -> str:
    arch = gpt([
        {"role": "system", "content": ADDITIVE_ARCHITECTURE_SYSTEM},
        {"role": "user", "content": (
            f"## EXISTING APP SUMMARY\n{existing_app_summary}\n\n"
            f"## NEW FEATURE REQUIREMENTS\n{new_feature_requirements}\n\n"
            f"## CHANGE GAP ANALYSIS\n{gap_analysis}\n\n"
            "Write the additive architecture."
        )},
    ])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "additive_architecture.md").write_text(arch, encoding="utf-8")
    return arch


# ── Feature Sprint Plan ────────────────────────────────────────────────────────

FEATURE_SPRINT_PLAN_SYSTEM = """You are a senior staff engineer splitting NEW FEATURE WORK for an \
EXISTING application into small, additive, independently-buildable feature sprints.

You are given the existing app summary, the new feature requirements, the change gap analysis, and \
the additive architecture. Sprint 0 (the existing app baseline) already exists and is handled outside \
of your output — you must NEVER describe Sprint 0 or re-describe the whole existing app from scratch. \
Only plan the NEW feature work, numbered starting at 1.

Design rules:
- Each sprint adds ONE clear new capability on top of the existing app (and on top of earlier sprints).
- Each sprint must be additive: it should not require rewriting unrelated existing code.
- Each sprint must leave the app in a runnable state — no sprint should be a "half feature".
- Order sprints by real dependency order (e.g. a persistence layer sprint before a feature that needs \
  persisted data; a roles/auth sprint before role-gated features).
- depends_on must include 0 (the baseline) for every sprint, plus any earlier feature sprint numbers \
  it actually requires.
- must_not_modify should name specific existing files/areas (from the additive architecture's "Files \
  That Should NOT Be Touched") that this sprint must leave alone.
- Sprint count should match the actual number of distinct requested features/capabilities — do not \
  pad or compress artificially. Typically 2-6 feature sprints.

Output STRICT JSON ONLY — no markdown fences, no prose before or after — matching exactly this shape:

{
  "product_name": "<short product name, from context>",
  "reason_for_split": "<1-2 sentences on why the work was split this way>",
  "sprints": [
    {
      "sprint_number": 1,
      "title": "<short sprint title>",
      "goal": "<1-2 sentences: what new capability this sprint adds>",
      "features": ["<specific feature this sprint delivers>", "..."],
      "depends_on": [0],
      "status": "ready",
      "buildable": true,
      "likely_files_created": ["<path>", "..."],
      "likely_files_modified": ["<path>", "..."],
      "must_not_modify": ["<path or area>", "..."],
      "completion_criteria": ["<concrete, checkable criterion>", "..."]
    }
  ]
}

Field rules:
- "sprint_number" starts at 1 and increments by 1, no gaps, no Sprint 0 entry.
- "status" is "ready" if depends_on are all satisfied by baseline + earlier sprints, else "locked".
- "buildable" is true for every feature sprint (Sprint 0 is the only non-buildable sprint, and it is \
  not part of your output).
- Every field is required for every sprint. Do not omit fields.
"""


_FEATURE_SPRINT_FIELD_DEFAULTS = {
    "title": "",
    "goal": "",
    "features": [],
    "depends_on": [0],
    "status": "ready",
    "buildable": True,
    "likely_files_created": [],
    "likely_files_modified": [],
    "must_not_modify": [],
    "completion_criteria": [],
}


def normalize_feature_sprint_plan(data: dict, existing_app_summary: str) -> dict:
    """Deterministic — fills defaults, coerces types, prepends the immutable Sprint 0
    baseline regardless of what the model produced. No GPT call."""
    sprints_in = data.get("sprints") or []
    normalized = []
    for i, raw in enumerate(sprints_in):
        entry = dict(_FEATURE_SPRINT_FIELD_DEFAULTS)
        entry.update(raw or {})
        try:
            entry["sprint_number"] = int(raw.get("sprint_number", i + 1))
        except (TypeError, ValueError):
            entry["sprint_number"] = i + 1
        if entry["sprint_number"] <= 0:
            entry["sprint_number"] = i + 1
        for k in ("features", "likely_files_created", "likely_files_modified",
                  "must_not_modify", "completion_criteria"):
            entry[k] = _coerce_list(entry[k])
        entry["depends_on"] = sorted(set(
            [0] + [int(d) for d in _coerce_list(entry["depends_on"]) if str(d).strip().lstrip("-").isdigit()]
        ))
        entry["buildable"] = True
        if entry.get("status") not in ("ready", "locked"):
            entry["status"] = "ready"
        normalized.append(entry)
    normalized.sort(key=lambda s: s["sprint_number"])

    baseline = {
        "sprint_number": 0,
        "title": "Baseline Existing App",
        "status": "complete",
        "buildable": False,
        "description": "Existing app before feature work. Used as the regression target — "
                        "never rebuilt, never described as new.",
    }

    return {
        "mode": "existing_app_upgrade",
        "product_name": data.get("product_name", ""),
        "reason_for_split": str(data.get("reason_for_split") or "").strip(),
        "baseline": baseline,
        "sprints": normalized,
        "total_sprints": len(normalized),
    }


def parse_feature_sprint_plan_json(raw_text: str, existing_app_summary: str) -> dict:
    candidate = _extract_json_object(raw_text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise SprintPlanParseError(
            f"Could not parse feature sprint plan JSON: {e}\n\nRaw model output:\n{raw_text[:1500]}"
        )
    if not isinstance(data, dict) or not data.get("sprints"):
        raise SprintPlanParseError(
            f"Feature sprint plan JSON is missing a non-empty 'sprints' list.\n\n"
            f"Raw model output:\n{raw_text[:1500]}"
        )
    return normalize_feature_sprint_plan(data, existing_app_summary)


def select_feature_sprint(plan_json: dict, selected_sprint_number: int) -> dict:
    """Deterministic lookup. Sprint 0 is the baseline and is never selectable for build."""
    if selected_sprint_number == 0:
        raise SprintNotFoundError(
            "Sprint 0 is the existing baseline and is not buildable. Choose a feature sprint >= 1."
        )
    for s in plan_json.get("sprints", []):
        if s.get("sprint_number") == selected_sprint_number:
            return s
    available = [s.get("sprint_number") for s in plan_json.get("sprints", [])]
    raise SprintNotFoundError(
        f"Feature sprint {selected_sprint_number} not found in plan (available: {available})"
    )


def render_feature_sprint_plan_markdown(plan_json: dict) -> str:
    """Deterministic — feature_sprint_plan.md. No GPT call."""
    sprints = sorted(plan_json.get("sprints", []), key=lambda s: s.get("sprint_number", 0))
    total = plan_json.get("total_sprints", len(sprints))
    product = plan_json.get("product_name", "")
    baseline = plan_json.get("baseline", {})
    lines = [f"# Feature Sprint Plan{f': {product}' if product else ''}", ""]
    if plan_json.get("reason_for_split"):
        lines.append(f"**Reason for split:** {plan_json['reason_for_split']}")
        lines.append("")
    lines.append(f"Total feature sprints: {total} (plus Sprint 0 baseline)")
    lines.append("")
    lines.append(f"## Sprint 0: {baseline.get('title', 'Baseline Existing App')}")
    lines.append("")
    lines.append(f"**Status:** {baseline.get('status', 'complete')} (not buildable)")
    lines.append("")
    lines.append(baseline.get("description", ""))
    lines.append("")
    lines.append("---")
    lines.append("")
    for s in sprints:
        n = s.get("sprint_number")
        lines.append(f"## Sprint {n} of {total}: {s.get('title', '')}")
        lines.append("")
        lines.append(f"**Goal:** {s.get('goal', '')}")
        lines.append("")
        lines.append("**Features:**")
        for f in s.get("features") or ["(not specified)"]:
            lines.append(f"- {f}")
        lines.append("")
        deps = s.get("depends_on") or [0]
        lines.append(f"**Depends on:** {', '.join('Sprint ' + str(d) for d in deps)}")
        lines.append("")
        lines.append(f"**Status:** {s.get('status', 'ready')}")
        lines.append("")
        lines.append("**Likely files created:**")
        for f in s.get("likely_files_created") or ["(not specified)"]:
            lines.append(f"- {f}")
        lines.append("")
        lines.append("**Likely files modified:**")
        for f in s.get("likely_files_modified") or ["(none)"]:
            lines.append(f"- {f}")
        lines.append("")
        lines.append("**Must not modify:**")
        for f in s.get("must_not_modify") or ["(none specified)"]:
            lines.append(f"- {f}")
        lines.append("")
        lines.append("**Completion criteria:**")
        for c in s.get("completion_criteria") or ["(not specified)"]:
            lines.append(f"- {c}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_feature_sprint_plan_terminal(plan_json: dict, selected_sprint_number: int) -> str:
    sprints = sorted(plan_json.get("sprints", []), key=lambda s: s.get("sprint_number", 0))
    total = plan_json.get("total_sprints", len(sprints))
    lines = ["Existing App Upgrade — Feature Sprint Plan", ""]
    lines.append("Sprint 0: Baseline Existing App (not buildable)")
    for s in sprints:
        n = s.get("sprint_number")
        marker = " <-- SELECTED" if n == selected_sprint_number else ""
        lines.append(f"Sprint {n} of {total}: {s.get('title', '')}{marker}")
        lines.append(f"  Goal: {s.get('goal', '')}")
    lines.append("")
    lines.append(f"Selected Feature Sprint: Sprint {selected_sprint_number} of {total}")
    return "\n".join(lines)


def generate_feature_sprint_plan(
    existing_app_summary: str,
    new_feature_requirements: str,
    gap_analysis: str,
    additive_architecture: str,
    run_dir,
    selected_sprint_number: int | None = None,
) -> tuple[dict, str]:
    """Uses GPT4O_MODEL (same model tier as the normal sprint architect). Writes
    feature_sprint_plan.json and feature_sprint_plan.md into run_dir."""
    user_msg = (
        f"## EXISTING APP SUMMARY\n{existing_app_summary}\n\n"
        f"## NEW FEATURE REQUIREMENTS\n{new_feature_requirements}\n\n"
        f"## CHANGE GAP ANALYSIS\n{gap_analysis}\n\n"
        f"## ADDITIVE ARCHITECTURE\n{additive_architecture}\n\n"
        "Now produce the feature sprint decomposition. Output STRICT JSON ONLY — no markdown, "
        "no commentary, no code fences."
    )
    raw = gpt4o([
        {"role": "system", "content": FEATURE_SPRINT_PLAN_SYSTEM},
        {"role": "user", "content": user_msg},
    ])
    plan_json = parse_feature_sprint_plan_json(raw, existing_app_summary)
    if selected_sprint_number is not None:
        plan_json["selected_feature_sprint"] = selected_sprint_number
    plan_md = render_feature_sprint_plan_markdown(plan_json)

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "feature_sprint_plan.json").write_text(json.dumps(plan_json, indent=2), encoding="utf-8")
    (run_dir / "feature_sprint_plan.md").write_text(plan_md, encoding="utf-8")
    return plan_json, plan_md


def render_selected_feature_sprint_scope_markdown(selected_sprint: dict, plan_json: dict) -> str:
    total = plan_json.get("total_sprints", len(plan_json.get("sprints", [])))
    n = selected_sprint.get("sprint_number")
    lines = [
        f"# Selected Feature Sprint Scope — Sprint {n} of {total}",
        "",
        f"## Title\n{selected_sprint.get('title', '')}",
        "",
        f"## Goal\n{selected_sprint.get('goal', '')}",
        "",
        "## Features To Build",
    ]
    for f in selected_sprint.get("features") or ["(not specified)"]:
        lines.append(f"- {f}")
    deps = selected_sprint.get("depends_on") or [0]
    lines += ["", "## Dependencies", ", ".join(f"Sprint {d}" for d in deps)]
    lines += ["", "## Allowed Files / Areas (may create)"]
    for f in selected_sprint.get("likely_files_created") or ["(use judgment within the architecture)"]:
        lines.append(f"- {f}")
    lines += ["", "## Allowed Files / Areas (may modify)"]
    for f in selected_sprint.get("likely_files_modified") or ["(none specified)"]:
        lines.append(f"- {f}")
    lines += ["", "## Protected Files / Areas (must not modify)"]
    for f in selected_sprint.get("must_not_modify") or ["(none specified)"]:
        lines.append(f"- {f}")
    lines += ["", "## Completion Criteria"]
    for c in selected_sprint.get("completion_criteria") or ["(not specified)"]:
        lines.append(f"- {c}")
    lines += [
        "", "## Regression Expectations",
        "- All existing behavior outside this sprint's scope must continue to work unchanged.",
        "- Protected files/areas listed above must not be modified — verified by regression_check.md.",
    ]
    return "\n".join(lines) + "\n"


def generate_selected_feature_sprint_build_prompt(
    existing_app_summary: str,
    scan: dict,
    plan_json: dict,
    selected_sprint: dict,
    run_dir,
) -> str:
    """
    Deterministic template — NO GPT call. This is the strict prompt Claude Code
    receives for Existing App Upgrade mode. It must never read like a from-scratch
    build prompt, and it must heavily discourage unrelated rewrites.

    Writes selected_feature_sprint_scope.md and selected_feature_sprint_build_prompt.txt.
    """
    total = plan_json.get("total_sprints", len(plan_json.get("sprints", [])))
    num = selected_sprint.get("sprint_number")
    title = selected_sprint.get("title") or f"Sprint {num}"
    all_sprints = sorted(plan_json.get("sprints", []), key=lambda s: s.get("sprint_number", 0))
    future_sprints = [s for s in all_sprints if s.get("sprint_number", 0) > num]
    earlier_sprints = [s for s in all_sprints if 0 < s.get("sprint_number", 0) < num]

    may_create = selected_sprint.get("likely_files_created") or []
    may_modify = selected_sprint.get("likely_files_modified") or []
    must_not_modify = selected_sprint.get("must_not_modify") or []

    parts = [
        "## CONTEXT: EXISTING APP",
        "You are extending an existing application. Do NOT rewrite it.",
        "",
        existing_app_summary,
        "",
        "## CURRENT TECH STACK",
        ", ".join(scan.get("tech_stack") or ["Unknown"]),
        "",
        f"## SELECTED FEATURE SPRINT — Sprint {num} of {total}: {title}",
        f"Goal: {selected_sprint.get('goal', '')}",
        "",
        "Features to build in this sprint:",
    ]
    for f in selected_sprint.get("features") or ["(see goal above)"]:
        parts.append(f"- {f}")

    if earlier_sprints:
        parts += ["", "## EARLIER FEATURE SPRINTS (assume already built)"]
        for s in earlier_sprints:
            parts.append(f"- Sprint {s.get('sprint_number')}: {s.get('title', '')} — already exists; "
                          "do not rebuild it, but you may use/extend it.")

    parts += [
        "",
        "## YOU MAY CREATE",
    ]
    for f in may_create or ["(use judgment, but prefer new files over editing existing ones)"]:
        parts.append(f"- {f}")
    parts += ["", "## YOU MAY MODIFY"]
    for f in may_modify or ["(none — prefer creating new files instead)"]:
        parts.append(f"- {f}")
    parts += ["", "## YOU MUST NOT MODIFY"]
    for f in must_not_modify or ["(no specific files flagged — still apply PRESERVATION RULES below)"]:
        parts.append(f"- {f}")
    parts += ["", "## YOU MUST NOT DELETE", "Any existing file not explicitly listed under YOU MAY MODIFY above."]

    parts += [
        "",
        "## PRESERVATION RULES",
        "- Preserve existing behavior unless this sprint explicitly requires changing it.",
        "- Do not rewrite unrelated files.",
        "- Do not change the tech stack.",
        "- Reuse existing style/components/patterns where possible.",
        "- Do not touch files outside this sprint's scope, even if they look improvable.",
        "- Do not add a backend, database, or auth unless this sprint's scope explicitly requires it.",
    ]

    if future_sprints:
        parts += ["", "## FUTURE FEATURE SPRINTS — REFERENCE ONLY, DO NOT BUILD"]
        for s in future_sprints:
            parts.append(f"- Sprint {s.get('sprint_number')}: {s.get('title', '')} — "
                          f"{s.get('goal', '')} (NOT in scope now.)")

    parts += ["", "## COMPLETION CRITERIA"]
    for c in selected_sprint.get("completion_criteria") or ["(not specified)"]:
        parts.append(f"- {c}")

    parts += [
        "",
        "## REGRESSION REQUIREMENTS",
        "- All existing pages/routes/components/behavior must continue to work exactly as before.",
        "- Do not remove or rename existing exports/functions/routes that other code may depend on.",
        "- Keep the app runnable end-to-end after this sprint, the same way it was runnable before.",
        "",
        "## AFTER YOU FINISH",
        "Print a clear summary of every file you created and every file you modified, and confirm "
        "you did not touch anything under YOU MUST NOT MODIFY.",
    ]

    prompt_text = "\n".join(parts)

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    scope_md = render_selected_feature_sprint_scope_markdown(selected_sprint, plan_json)
    (run_dir / "selected_feature_sprint_scope.md").write_text(scope_md, encoding="utf-8")
    (run_dir / "selected_feature_sprint_build_prompt.txt").write_text(prompt_text, encoding="utf-8")
    (run_dir / f"feature_sprint_{num}_scope.md").write_text(scope_md, encoding="utf-8")
    (run_dir / f"feature_sprint_{num}_build_prompt.txt").write_text(prompt_text, encoding="utf-8")
    return prompt_text


# ── Regression Protection ──────────────────────────────────────────────────────

def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _resolve_protected_paths(existing_app_path: Path, patterns: list[str]) -> list[Path]:
    existing_app_path = Path(existing_app_path)
    resolved: list[Path] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            resolved.extend(existing_app_path.glob(pattern))
        else:
            p = existing_app_path / pattern
            if p.is_file():
                resolved.append(p)
            elif p.is_dir():
                resolved.extend(f for f in p.rglob("*") if f.is_file()
                                and not any(part in _SCAN_IGNORE_DIRS for part in f.parts))
    return [p for p in dict.fromkeys(resolved) if p.is_file()]


def snapshot_protected_files(existing_app_path: Path, must_not_modify: list[str], run_dir) -> dict:
    """Deterministic — hashes every protected file BEFORE build so regression_check
    can detect unexpected changes afterward. Writes baseline_file_hashes.json."""
    files = _resolve_protected_paths(existing_app_path, must_not_modify)
    hashes = {}
    for f in files:
        try:
            hashes[str(f.relative_to(existing_app_path))] = _hash_file(f)
        except Exception:
            pass
    record = {"protected_patterns": must_not_modify, "hashes": hashes}
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "baseline_file_hashes.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def run_regression_check(
    existing_app_path: Path,
    run_dir,
    selected_sprint: dict,
    smoke_log: str = "",
) -> tuple[str, str]:
    """
    Compares protected-file hashes before vs after build. PASS if none of the
    protected files changed and expected new files exist (where checkable);
    FAIL if any protected file changed unexpectedly; UNKNOWN if there was no
    baseline to compare against (e.g. no must_not_modify list was given).
    Writes regression_check.md. Returns (status, report_text).
    """
    run_dir = Path(run_dir)
    existing_app_path = Path(existing_app_path)
    baseline_path = run_dir / "baseline_file_hashes.json"
    if not baseline_path.exists():
        status = "UNKNOWN"
        changed, missing = [], []
        baseline_hashes = {}
    else:
        baseline = _safe_read_json(baseline_path)
        baseline_hashes = baseline.get("hashes", {})
        changed, missing = [], []
        if not baseline_hashes:
            status = "UNKNOWN"
        else:
            for rel, old_hash in baseline_hashes.items():
                p = existing_app_path / rel
                if not p.exists():
                    missing.append(rel)
                    continue
                try:
                    new_hash = _hash_file(p)
                except Exception:
                    missing.append(rel)
                    continue
                if new_hash != old_hash:
                    changed.append(rel)
            status = "FAIL" if (changed or missing) else "PASS"

    expected_new = selected_sprint.get("likely_files_created") or []
    confirmed_new, missing_new = [], []
    for f in expected_new:
        if any(ch in f for ch in "*?["):
            continue  # glob hints aren't checkable as exact paths
        (confirmed_new if (existing_app_path / f).exists() else missing_new).append(f)

    lines = ["# Regression Check", "", f"**Status:** {status}", ""]
    lines.append("## Protected Files Checked")
    lines.append(f"{len(baseline_hashes)} file(s) tracked from must_not_modify list.")
    lines.append("")
    lines.append("## Unexpectedly Changed Protected Files")
    for f in changed or ["(none)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Missing Protected Files")
    for f in missing or ["(none)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Expected New Files")
    lines.append(f"Confirmed present: {len(confirmed_new)}/{len(expected_new)}")
    for f in missing_new:
        lines.append(f"- MISSING expected new file: {f}")
    lines.append("")
    if smoke_log:
        lines.append("## Smoke / Runtime Check Results")
        lines.append("```")
        lines.append(smoke_log[:2000])
        lines.append("```")
        lines.append("")
    if status == "UNKNOWN":
        lines.append("## Note")
        lines.append(
            "No protected-file baseline was available to compare against (the selected sprint's "
            "must_not_modify list was empty, or no tests exist for this app). Regression confidence "
            "for this run is limited to the static/expected-file checks above."
        )
    content = "\n".join(lines) + "\n"
    (run_dir / "regression_check.md").write_text(content, encoding="utf-8")
    return status, content


FEATURE_COMPLETION_REPORT_SYSTEM = """You are writing the handoff summary for ONE completed feature \
sprint built on top of an existing application. Be practical, honest, and specific. Never describe \
this as a "new MVP" — this app already existed before this sprint.

Write in this exact format:

# Feature Completion Report

## Feature Sprint Completed
Name and number of the sprint.

## What Was Added
Bullet list of what this sprint actually added.

## What Existing Behavior Was Preserved
Bullet list, grounded in the regression check.

## Files Created / Modified / Deleted
Summarize from the build output and regression check.

## Regression Result
State the PASS/FAIL/UNKNOWN result and what it means.

## Smoke Check Result
State the result if available, or "Not run."

## What Was Intentionally Not Touched
Bullet list, from the must_not_modify list.

## Next Recommended Feature Sprint
Name the next sprint from the plan and why it comes next.

## Warnings / Risks
Bullet list. Be honest — include anything regression flagged.
"""


def generate_feature_completion_report(
    existing_app_summary: str,
    plan_json: dict,
    selected_sprint: dict,
    build_output: str,
    regression_status: str,
    regression_report: str,
    smoke_log: str,
    run_dir,
) -> str:
    all_sprints = sorted(plan_json.get("sprints", []), key=lambda s: s.get("sprint_number", 0))
    n = selected_sprint.get("sprint_number")
    next_sprint = next((s for s in all_sprints if s.get("sprint_number") == n + 1), None)
    next_sprint_label = (
        f"Sprint {next_sprint.get('sprint_number')}: {next_sprint.get('title', '')}"
        if next_sprint else "(none — this was the last planned feature sprint)"
    )
    report = gpt([
        {"role": "system", "content": FEATURE_COMPLETION_REPORT_SYSTEM},
        {"role": "user", "content": (
            f"## EXISTING APP SUMMARY\n{existing_app_summary}\n\n"
            f"## SELECTED SPRINT\nSprint {n}: {selected_sprint.get('title', '')}\n"
            f"Goal: {selected_sprint.get('goal', '')}\n"
            f"Must not modify: {selected_sprint.get('must_not_modify') or '(none specified)'}\n\n"
            f"## NEXT SPRINT (if any)\n{next_sprint_label}\n\n"
            f"## CLAUDE CODE BUILD OUTPUT (tail)\n{build_output[-4000:]}\n\n"
            f"## REGRESSION STATUS: {regression_status}\n{regression_report}\n\n"
            f"## SMOKE LOG\n{smoke_log[:2000]}\n\n"
            "Write the feature completion report."
        )},
    ])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "feature_completion_report.md").write_text(report, encoding="utf-8")
    return report


def build_feature_sprint(run_id: str, existing_app_path: Path, build_prompt_text: str) -> str:
    """Runs Claude Code IN PLACE inside the existing app directory — never creates a
    fresh app folder, unlike build_mvp() for normal mode."""
    full_prompt = (
        f"{build_prompt_text}\n\n"
        "---\n"
        "You are working inside an EXISTING application's directory. Do not create a new project "
        "at the top level. Add files relative to the existing structure you can already see here.\n"
        "After building, print a summary of every file you created and every file you modified.\n"
    )
    output = _stream_subprocess(
        CLAUDE_CODE_CMD + [full_prompt],
        cwd=str(existing_app_path),
        timeout=CLAUDE_TIMEOUT,
    )
    save_artifact(run_id, "claude_build_output.txt", output)
    log_event(run_id, "claude_build_feature_sprint", output[:500])
    return output


def pipeline_existing_app_upgrade(
    existing_app_path: str,
    feature_request_text: str,
    run_id: str | None = None,
    selected_feature_sprint: int = 1,
    feature_plan_only: bool = False,
    use_deepseek: bool = True,
) -> str:
    """
    Existing App Upgrade mode entry point. Additive, not generative-from-scratch:
    inspect -> summarize -> normalize feature request -> gap analysis -> additive
    architecture -> feature sprint plan -> build ONE selected feature sprint ->
    regression check -> (optional) review/fix -> feature completion report.
    """
    existing_app_path = Path(existing_app_path).resolve()
    if not existing_app_path.exists():
        raise FileNotFoundError(f"--existing-app path does not exist: {existing_app_path}")

    if not run_id:
        run_id = next_run_id()
    rdir = run_dir(run_id)
    init_run(run_id, f"Existing App Upgrade — {existing_app_path}\n\n{feature_request_text}")
    save_artifact(run_id, "feature_request.md", feature_request_text)
    _update_state(run_id, {"mode": "existing_app_upgrade", "existing_app_path": str(existing_app_path)})

    print(f"\n{'='*60}")
    print("  Existing App Upgrade Mode")
    print(f"  Run folder    : {rdir}")
    print(f"  Existing app  : {existing_app_path}")
    print(f"  DeepSeek      : {'enabled' if use_deepseek and DEEPSEEK_API_KEY else 'disabled (no key)'}")
    print(f"  Plan only     : {feature_plan_only}")
    print(f"{'='*60}\n")

    print("▶ Step 1  Scanning existing app...")
    t0 = time.time()
    scan = scan_existing_app(existing_app_path)
    inventory_md = write_existing_app_inventory(scan, rdir)
    record_step_time(run_id, "existing_app_scan", t0)
    print(f"  Detected stack: {', '.join(scan['tech_stack'])}")

    print("▶ Step 2  Checking baseline health...")
    t0 = time.time()
    health_md = run_baseline_health_check(existing_app_path, scan, rdir)
    record_step_time(run_id, "baseline_health_check", t0)
    health_status = "UNKNOWN"
    m = re.search(r"\*\*Status:\*\*\s*(\w+)", health_md)
    if m:
        health_status = m.group(1)
    print(f"  Baseline health: {health_status}")
    if health_status == "BROKEN":
        print("  ⚠️  WARNING: the existing app appears to have pre-existing errors. "
              "Continuing — see baseline_health_check.md.")

    print("▶ Step 3  Writing existing app summary...")
    t0 = time.time()
    existing_app_summary = generate_existing_app_summary(inventory_md, health_md, rdir)
    record_step_time(run_id, "existing_app_summary", t0)

    print("▶ Step 4  Normalizing requested features...")
    t0 = time.time()
    feature_requirements = generate_new_feature_requirements(feature_request_text, existing_app_summary, rdir)
    record_step_time(run_id, "feature_requirements", t0)

    print("▶ Step 5  Writing change gap analysis...")
    t0 = time.time()
    gap_analysis = generate_change_gap_analysis(existing_app_summary, feature_requirements, inventory_md, rdir)
    record_step_time(run_id, "gap_analysis", t0)

    print("▶ Step 6  Creating additive architecture...")
    t0 = time.time()
    additive_architecture = generate_additive_architecture(existing_app_summary, feature_requirements, gap_analysis, rdir)
    record_step_time(run_id, "additive_architecture", t0)

    print("▶ Step 7  Creating feature sprint plan...")
    t0 = time.time()
    plan_json, _plan_md = generate_feature_sprint_plan(
        existing_app_summary, feature_requirements, gap_analysis, additive_architecture,
        rdir, selected_sprint_number=selected_feature_sprint,
    )
    record_step_time(run_id, "feature_sprint_plan", t0)
    _update_state(run_id, {})
    print(f"\n{render_feature_sprint_plan_terminal(plan_json, selected_feature_sprint)}\n")

    selected_sprint = select_feature_sprint(plan_json, selected_feature_sprint)
    total = plan_json.get("total_sprints", len(plan_json.get("sprints", [])))
    print(f"Selected Feature Sprint: Sprint {selected_feature_sprint} of {total}")

    print("▶ Step 8  Writing selected feature sprint scope + build prompt...")
    build_prompt_text = generate_selected_feature_sprint_build_prompt(
        existing_app_summary, scan, plan_json, selected_sprint, rdir,
    )
    _update_state(run_id, {})

    if feature_plan_only:
        _update_state(run_id, {"status": "feature_plan_only_done", "current_step": "done"})
        log_event(run_id, "feature_plan_only_done")
        print(f"\n{'='*60}")
        print("  📝  Feature-plan-only run complete — planning artifacts generated. "
              "No Claude Code or DeepSeek calls made.")
        print(f"  Run folder : {rdir}")
        print("  Artifacts  :")
        for f in sorted(rdir.iterdir()):
            if f.is_file():
                print(f"        {f.name}")
        print(f"{'='*60}\n")
        return run_id

    must_not_modify = selected_sprint.get("must_not_modify") or []
    snapshot_protected_files(existing_app_path, must_not_modify, rdir)

    print(f"\n▶ Step 9  Claude Code — building Sprint {selected_feature_sprint} in place...")
    t0 = time.time()
    _update_state(run_id, {"current_step": "building", "status": "building"})
    build_output = build_feature_sprint(run_id, existing_app_path, build_prompt_text)
    record_step_time(run_id, "built", t0)
    _update_state(run_id, {"status": "built"})

    print("\n▶ Step 10  Regression check...")
    smoke_log = ""
    try:
        smoke_log = run_smoke_checks(run_id, existing_app_path)
    except Exception as e:
        smoke_log = f"Smoke checks could not run: {e}"
    save_artifact(run_id, "feature_sprint_smoke_log.txt", smoke_log)
    regression_status, regression_report = run_regression_check(
        existing_app_path, rdir, selected_sprint, smoke_log,
    )
    print(f"  Regression result: {regression_status}")
    if regression_status == "FAIL":
        print("  ⚠️  WARNING: unexpected changes to protected files were detected.")

    if use_deepseek and DEEPSEEK_API_KEY:
        print("\n▶ Step 11  DeepSeek review (optional)...")
        deepseek_report = deepseek_attack_review(existing_app_summary, existing_app_path, smoke_log + "\n\n" + regression_report)
        save_artifact(run_id, "deepseek_attack_report.md", deepseek_report)
        if not is_approved(deepseek_report):
            print("  Applying one fix iteration based on DeepSeek review...")
            fix_prompt = generate_fix_prompt(existing_app_summary, existing_app_path, deepseek_report, 1)
            apply_fixes(run_id, existing_app_path, fix_prompt, 1)
            smoke_log = run_smoke_checks(run_id, existing_app_path)
            regression_status, regression_report = run_regression_check(
                existing_app_path, rdir, selected_sprint, smoke_log,
            )
            print(f"  Regression result after fix: {regression_status}")

    print("\n▶ Step 12  Writing feature completion report...")
    generate_feature_completion_report(
        existing_app_summary, plan_json, selected_sprint, build_output,
        regression_status, regression_report, smoke_log, rdir,
    )

    _update_state(run_id, {"status": "done", "current_step": "done", "regression_status": regression_status})
    log_event(run_id, "done", f"regression={regression_status}")

    print(f"\n{'='*60}")
    print("  Existing App Upgrade — Feature Sprint Complete")
    print(f"  Run folder : {rdir}")
    print(f"  Regression : {regression_status}")
    print("  Artifacts  :")
    for f in sorted(rdir.iterdir()):
        if f.is_file():
            print(f"        {f.name}")
    print(f"{'='*60}\n")
    return run_id


# ═════════════════════════════════════════════════════════════════════════════
# Multi-Sprint Continuation Mode
# ═════════════════════════════════════════════════════════════════════════════
# A third entry point alongside the normal "idea -> new MVP" pipeline and the
# Existing App Upgrade pipeline. Given a previous run folder + a requested next
# sprint number, this mode LOADS (never regenerates) that run's preserved sprint
# plan, copies that run's app baseline into a brand-new run folder (never
# mutating the source run), builds ONLY the requested next sprint on top of it,
# checks regression against everything previously completed, and writes a
# continuation completion report. Works for both normal sprint-mode runs
# (sprint_plan.json + mvp/) and Existing App Upgrade runs (feature_sprint_plan.json).

class ContinuationError(RuntimeError):
    """Raised when a --continue-run source run is missing required artifacts, or
    when no buildable app baseline can be found for it."""


def detect_continuation_source(source_run_path: Path) -> dict:
    """
    Deterministic — inspects a previous run folder and classifies it as normal
    sprint mode or Existing App Upgrade mode, without regenerating or modifying
    anything in it. No GPT call. Raises ContinuationError if no sprint plan can
    be found at all (the explicit hard-failure case the operator asked for).
    """
    source_run_path = Path(source_run_path).resolve()
    if not source_run_path.exists():
        raise ContinuationError(f"--continue-run path does not exist: {source_run_path}")

    state = _safe_read_json(source_run_path / "run_state.json")

    has_sprint_plan = (source_run_path / "sprint_plan.json").exists()
    has_feature_sprint_plan = (source_run_path / "feature_sprint_plan.json").exists()
    has_mvp_dir = (source_run_path / "mvp").is_dir()

    check_names = (
        "sprint_plan.json", "feature_sprint_plan.json", "mvp",
        "selected_sprint_scope.md", "selected_feature_sprint_scope.md",
        "final_mvp_report.md", "feature_completion_report.md", "regression_check.md",
    )
    found = [n for n in check_names if (source_run_path / n).exists()]
    missing = [n for n in check_names if n not in found]

    if has_feature_sprint_plan:
        detected_mode = "existing_app_upgrade"
        sprint_plan_filename = "feature_sprint_plan.json"
        plan_json = _safe_read_json(source_run_path / sprint_plan_filename)
        previous_selected_sprint = (
            state.get("selected_feature_sprint")
            or plan_json.get("selected_feature_sprint")
        )
        existing_app_path = state.get("existing_app_path")
        app_path = Path(existing_app_path) if existing_app_path else None
    elif has_sprint_plan:
        detected_mode = "normal_sprint"
        sprint_plan_filename = "sprint_plan.json"
        plan_json = _safe_read_json(source_run_path / sprint_plan_filename)
        previous_selected_sprint = state.get("selected_sprint") or plan_json.get("selected_sprint")
        app_path = (source_run_path / "mvp") if has_mvp_dir else None
    else:
        raise ContinuationError(
            "Cannot continue because no sprint plan was found in the source run "
            f"({source_run_path}). Expected sprint_plan.json (normal mode) or "
            "feature_sprint_plan.json (Existing App Upgrade mode)."
        )

    return {
        "source_run": str(source_run_path),
        "detected_mode": detected_mode,
        "sprint_plan_filename": sprint_plan_filename,
        "previous_selected_sprint": previous_selected_sprint,
        "app_path": app_path,
        "state": state,
        "artifacts_found": found,
        "artifacts_missing": missing,
    }


def load_preserved_sprint_plan(source_info: dict) -> dict:
    """Loads the source run's sprint plan EXACTLY as written — no GPT call, no
    regeneration. Raises ContinuationError if the file is missing or empty."""
    plan_path = Path(source_info["source_run"]) / source_info["sprint_plan_filename"]
    if not plan_path.exists():
        raise ContinuationError(
            "Cannot continue because no sprint plan was found in the source run "
            f"({source_info['source_run']})."
        )
    plan_json = _safe_read_json(plan_path)
    if not plan_json.get("sprints"):
        raise ContinuationError(
            f"Cannot continue — {plan_path} exists but has no 'sprints' list to preserve."
        )
    return plan_json


# ── Mode-aware sprint-entry accessors ───────────────────────────────────────────
# Normal-mode sprint entries are keyed "number" with fields like
# files_modules_touched/smoke_checks; Existing-App-Upgrade entries are keyed
# "sprint_number" with fields like likely_files_created/must_not_modify. These
# small accessors let the rest of continuation mode treat both shapes uniformly
# without duplicating logic per mode.

def _sprint_key(detected_mode: str) -> str:
    return "sprint_number" if detected_mode == "existing_app_upgrade" else "number"


def _sprint_number(entry: dict, detected_mode: str) -> int:
    return entry.get(_sprint_key(detected_mode))


def _sprint_features(entry: dict, detected_mode: str) -> list:
    if detected_mode == "existing_app_upgrade":
        return entry.get("features") or []
    return entry.get("files_modules_touched") or []


def _sprint_files_created(entry: dict, detected_mode: str) -> list:
    if detected_mode == "existing_app_upgrade":
        return entry.get("likely_files_created") or []
    return entry.get("files_modules_touched") or []


def _sprint_files_modified(entry: dict, detected_mode: str) -> list:
    if detected_mode == "existing_app_upgrade":
        return entry.get("likely_files_modified") or []
    return []


def _sprint_must_not_modify(entry: dict, detected_mode: str) -> list:
    if detected_mode == "existing_app_upgrade":
        return entry.get("must_not_modify") or []
    return []


def _sprint_completion_criteria(entry: dict, detected_mode: str) -> list:
    if detected_mode == "existing_app_upgrade":
        return entry.get("completion_criteria") or []
    return entry.get("smoke_checks") or []


def _all_sprints(plan_json: dict, detected_mode: str) -> list:
    key = _sprint_key(detected_mode)
    return sorted(plan_json.get("sprints", []), key=lambda s: s.get(key, 0))


def select_continuation_sprint(plan_json: dict, detected_mode: str, selected_sprint_number: int) -> dict:
    """Deterministic, mode-aware lookup. Reuses the existing select_sprint /
    select_feature_sprint logic rather than reimplementing it."""
    if detected_mode == "existing_app_upgrade":
        return select_feature_sprint(plan_json, selected_sprint_number)
    return select_sprint(plan_json, selected_sprint_number)


def copy_baseline_app_for_continuation(source_info: dict, new_run_dir: Path) -> Path:
    """
    Copies the app baseline into the NEW run folder so the source run is NEVER
    mutated. Normal mode: copies source_run/mvp -> new_run/mvp. Existing App
    Upgrade mode: copies the original existing-app folder (recorded in the
    source run's state) -> new_run/app. Deterministic, no GPT call. Raises
    ContinuationError if no app folder exists to copy.
    """
    new_run_dir = Path(new_run_dir)
    app_path = source_info.get("app_path")
    if not app_path or not Path(app_path).exists():
        raise ContinuationError(
            "Cannot continue — no buildable app folder was found for the source run "
            f"({source_info['source_run']}). Expected mvp/ (normal mode) or the recorded "
            "existing-app path (Existing App Upgrade mode)."
        )
    app_path = Path(app_path)
    dest = new_run_dir / ("mvp" if source_info["detected_mode"] == "normal_sprint" else "app")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(app_path, dest, ignore=shutil.ignore_patterns(*_SCAN_IGNORE_DIRS))
    return dest


def write_preserved_sprint_plan_artifacts(plan_json: dict, detected_mode: str, run_dir) -> str:
    """Copies the previous sprint plan EXACTLY (preserved_sprint_plan.json) and
    renders it for humans (preserved_sprint_plan.md), clearly labeled as not
    regenerated. No GPT call."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "preserved_sprint_plan.json").write_text(json.dumps(plan_json, indent=2), encoding="utf-8")
    body = (
        render_feature_sprint_plan_markdown(plan_json)
        if detected_mode == "existing_app_upgrade"
        else render_sprint_plan_markdown(plan_json)
    )
    banner = (
        "> **Preserved from previous run. Not regenerated.**\n"
        "> This plan was loaded exactly as written by the source run and reused as-is "
        "for this continuation sprint.\n\n"
    )
    content = banner + body
    (run_dir / "preserved_sprint_plan.md").write_text(content, encoding="utf-8")
    return content


def write_continuation_source_artifact(
    source_info: dict, requested_sprint: int, app_path: Path, run_dir,
) -> str:
    """Deterministic — continuation_source.md. No GPT call."""
    lines = ["# Continuation Source", ""]
    lines.append(f"**Source run:** `{source_info['source_run']}`")
    lines.append(f"**Detected mode:** {source_info['detected_mode']}")
    lines.append(f"**Previous selected sprint:** {source_info.get('previous_selected_sprint') or '(unknown)'}")
    lines.append(f"**Requested next sprint:** {requested_sprint}")
    lines.append(f"**App baseline used (copied, not mutated):** `{app_path}`")
    lines.append(f"**Sprint plan file preserved:** `{source_info['sprint_plan_filename']}`")
    lines.append("")
    lines.append("## Important Previous Artifacts Found")
    for f in source_info["artifacts_found"] or ["(none)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Important Previous Artifacts Missing")
    for f in source_info["artifacts_missing"] or ["(none)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Safety Notes")
    lines.append("- The source run folder above was NOT modified by this continuation.")
    lines.append("- The sprint plan was loaded exactly as-is and was not regenerated.")
    content = "\n".join(lines) + "\n"
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "continuation_source.md").write_text(content, encoding="utf-8")
    return content


def write_current_app_inventory(scan: dict, run_dir) -> str:
    """Deterministic — renders the scan dict for a CONTINUATION run's COPIED app
    baseline into current_app_inventory.md. Same scanner as Existing App Upgrade
    mode (scan_existing_app), different filename so it never collides with that
    mode's existing_app_inventory.md. No GPT call."""
    lines = ["# Current App Inventory", "", f"**Root:** `{scan['root']}`", ""]
    lines.append(f"**Detected tech stack:** {', '.join(scan['tech_stack'])}")
    lines.append(f"**Package manager:** {scan['package_manager'] or 'Not detected'}")
    lines.append(f"**Frontend framework:** {scan['frontend_framework'] or 'None detected'}")
    lines.append(f"**Backend framework:** {scan['backend_framework'] or 'None detected'}")
    lines.append(f"**Database:** {scan['database'] or 'None detected'}")
    lines.append(f"**Auth:** {scan['auth'] or 'None detected'}")
    lines.append("")
    lines.append(f"**Total files scanned:** {scan['file_count']}")
    lines.append("")
    lines.append("## Folder Structure (top level)")
    for d in scan["top_level_dirs"] or ["(no subfolders)"]:
        lines.append(f"- {d}/")
    lines.append("")
    lines.append("## App Entry Points")
    for f in scan["entry_points"] or ["(none detected)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Components (if detectable)")
    for f in scan["components"] or ["(none detected)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## API Files (if detectable)")
    for f in scan["api_files"] or ["(none detected)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Test / Build Scripts")
    if scan["scripts"]:
        for k, v in scan["scripts"].items():
            lines.append(f"- `{k}`: `{v}`")
    else:
        lines.append("(none detected — no package.json scripts found)")
    lines.append("")
    content = "\n".join(lines) + "\n"
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "current_app_inventory.md").write_text(content, encoding="utf-8")
    return content


def scan_current_app_for_continuation(app_path: Path, run_dir) -> tuple[dict, str]:
    """Reuses the deterministic scan_existing_app() scanner against the COPIED
    app baseline for this continuation run. No GPT call."""
    scan = scan_existing_app(app_path)
    inventory_md = write_current_app_inventory(scan, run_dir)
    return scan, inventory_md


CONTINUATION_GAP_ANALYSIS_SYSTEM = """You are a staff engineer performing a change-impact analysis \
before building the NEXT sprint of a multi-sprint product that is already partway built. You are \
given the preserved sprint plan, what the most recently completed sprint was, what the next sprint is \
supposed to add, and the current app inventory.

Write in this exact format:

# Continuation Gap Analysis

## What Previous Sprint(s) Completed
Bullet list, grounded in the preserved sprint plan and current app inventory.

## What The Next Sprint Must Add
Bullet list of the next sprint's goal/features.

## Dependencies That Must Already Exist
Bullet list of things the next sprint assumes are already built. Flag anything that looks missing \
from the current app inventory.

## Files / Areas Likely Impacted
Bullet list of specific files/folders likely to be touched by the next sprint.

## Risks
Bullet list of concrete risks to existing functionality.

## Protected Prior-Sprint Functionality
Bullet list of specific previous-sprint behavior/files that must keep working unchanged.

Be specific and grounded in the actual inputs — do not write generic boilerplate. Never describe this \
as a new product being built from scratch; it already has completed sprints.
"""


def generate_continuation_gap_analysis(
    preserved_plan_md: str,
    previous_sprint: dict | None,
    next_sprint: dict,
    current_app_inventory_md: str,
    detected_mode: str,
    run_dir,
) -> str:
    prev_label = (
        f"Sprint {_sprint_number(previous_sprint, detected_mode)}: {previous_sprint.get('title', '')}"
        if previous_sprint else "(none on record — Sprint 0 / initial baseline only)"
    )
    next_num = _sprint_number(next_sprint, detected_mode)
    gap = gpt([
        {"role": "system", "content": CONTINUATION_GAP_ANALYSIS_SYSTEM},
        {"role": "user", "content": (
            f"## PRESERVED SPRINT PLAN\n{preserved_plan_md}\n\n"
            f"## MOST RECENTLY COMPLETED SPRINT\n{prev_label}\n\n"
            f"## NEXT SPRINT TO BUILD\nSprint {next_num}: {next_sprint.get('title', '')}\n"
            f"Goal: {next_sprint.get('goal', '')}\n\n"
            f"## CURRENT APP INVENTORY\n{current_app_inventory_md}\n\n"
            "Write the continuation gap analysis."
        )},
    ])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "continuation_gap_analysis.md").write_text(gap, encoding="utf-8")
    return gap


def render_continuation_sprint_scope_markdown(
    next_sprint: dict, previous_sprint: dict | None, detected_mode: str, plan_json: dict,
) -> str:
    """Deterministic — selected_continuation_sprint_scope.md. No GPT call."""
    total = plan_json.get("total_sprints", len(plan_json.get("sprints", [])))
    n = _sprint_number(next_sprint, detected_mode)
    lines = [
        f"# Selected Continuation Sprint Scope — Sprint {n} of {total}",
        "",
        f"## Title\n{next_sprint.get('title', '')}",
        "",
        f"## Goal\n{next_sprint.get('goal', '')}",
        "",
        "## Features / Scope",
    ]
    for f in _sprint_features(next_sprint, detected_mode) or ["(not specified)"]:
        lines.append(f"- {f}")
    lines += ["", "## Previous Sprint Work That Must Be Preserved"]
    if previous_sprint:
        pn = _sprint_number(previous_sprint, detected_mode)
        lines.append(f"- Sprint {pn}: {previous_sprint.get('title', '')} — already built; do not rebuild or remove.")
    else:
        lines.append("- Sprint 0 / initial baseline — already built; do not rebuild or remove.")
    lines += ["", "## Likely Files To Create"]
    for f in _sprint_files_created(next_sprint, detected_mode) or ["(use judgment within the preserved plan)"]:
        lines.append(f"- {f}")
    lines += ["", "## Likely Files To Modify"]
    for f in _sprint_files_modified(next_sprint, detected_mode) or ["(none specified)"]:
        lines.append(f"- {f}")
    lines += ["", "## Protected Files / Areas (must not modify)"]
    for f in _sprint_must_not_modify(next_sprint, detected_mode) or [
        "(none specified — still apply general preservation rules)"
    ]:
        lines.append(f"- {f}")
    lines += ["", "## Completion Criteria"]
    for c in _sprint_completion_criteria(next_sprint, detected_mode) or ["(not specified)"]:
        lines.append(f"- {c}")
    lines += [
        "", "## Regression Expectations",
        "- All functionality completed in previous sprints must continue to work unchanged.",
        "- Protected files/areas above must not be modified — verified by continuation_regression_check.md.",
    ]
    return "\n".join(lines) + "\n"


def generate_continuation_sprint_build_prompt(
    detected_mode: str,
    plan_json: dict,
    next_sprint: dict,
    previous_sprint: dict | None,
    current_app_scan: dict,
    run_dir,
) -> str:
    """
    Deterministic template — NO GPT call. The strict prompt Claude Code receives
    to build ONLY the next sprint on top of an already-partially-built product.
    Mirrors generate_selected_feature_sprint_build_prompt's structure but adds
    explicit multi-sprint continuation language (do not rebuild previous sprints,
    do not regenerate from scratch, keep the app runnable).

    Writes selected_continuation_sprint_scope.md and
    selected_continuation_sprint_build_prompt.txt into run_dir.
    """
    key = _sprint_key(detected_mode)
    total = plan_json.get("total_sprints", len(plan_json.get("sprints", [])))
    num = next_sprint.get(key)
    title = next_sprint.get("title") or f"Sprint {num}"
    all_sprints = _all_sprints(plan_json, detected_mode)
    earlier_sprints = [s for s in all_sprints if s.get(key, 0) < num]
    future_sprints = [s for s in all_sprints if s.get(key, 0) > num]

    may_create = _sprint_files_created(next_sprint, detected_mode)
    may_modify = _sprint_files_modified(next_sprint, detected_mode)
    must_not_modify = _sprint_must_not_modify(next_sprint, detected_mode)
    completion = _sprint_completion_criteria(next_sprint, detected_mode)

    parts = [
        "## CONTEXT: MULTI-SPRINT CONTINUATION",
        "You are continuing an EXISTING multi-sprint product. Previous sprints have already been "
        "built and are working. Do not rebuild Sprint 1 or any other previously completed sprint. "
        "Do not regenerate the app from scratch.",
        "",
        f"## CURRENT TECH STACK\n{', '.join(current_app_scan.get('tech_stack') or ['Unknown'])}",
        "",
        "## PREVIOUSLY COMPLETED SPRINT(S) — assume already built",
    ]
    if earlier_sprints:
        for s in earlier_sprints:
            parts.append(f"- Sprint {s.get(key)}: {s.get('title', '')} — already built; "
                          "do not rebuild it, but you may use/extend it.")
    else:
        parts.append("- Sprint 0 / initial baseline — already exists; do not rebuild it.")

    parts += [
        "",
        f"## BUILD ONLY THIS SPRINT — Sprint {num} of {total}: {title}",
        f"Goal: {next_sprint.get('goal', '')}",
        "",
        "Scope to build in this sprint:",
    ]
    for f in (_sprint_features(next_sprint, detected_mode) or ["(see goal above)"]):
        parts.append(f"- {f}")

    parts += ["", "## MAY CREATE"]
    for f in may_create or ["(use judgment, but prefer new files over editing existing ones)"]:
        parts.append(f"- {f}")
    parts += ["", "## MAY MODIFY"]
    for f in may_modify or ["(none — prefer creating new files instead)"]:
        parts.append(f"- {f}")
    parts += ["", "## MUST PRESERVE PREVIOUS SPRINT BEHAVIOR"]
    parts.append("- Everything listed under PREVIOUSLY COMPLETED SPRINT(S) above must keep working "
                  "exactly as it does now.")
    for f in must_not_modify:
        parts.append(f"- {f}")
    parts += [
        "", "## MUST NOT DELETE EXISTING WORKING FEATURES",
        "Any file or feature not explicitly listed under MAY MODIFY above.",
    ]

    if future_sprints:
        parts += ["", "## FUTURE SPRINTS — REFERENCE ONLY, DO NOT BUILD"]
        for s in future_sprints:
            parts.append(f"- Sprint {s.get(key)}: {s.get('title', '')} — {s.get('goal', '')} "
                          f"(NOT in scope now. Do not implement Sprint {s.get(key)}.)")

    parts += ["", "## COMPLETION CRITERIA"]
    for c in completion or ["(not specified)"]:
        parts.append(f"- {c}")

    parts += [
        "",
        "## REGRESSION CHECKLIST (must hold true after this sprint)",
        "- The app still builds/runs after this sprint.",
        "- All functionality from previous sprints still works exactly as before.",
        "- You did not rewrite unrelated files.",
        "- You did not implement any future sprint listed above.",
        "- The app remains runnable end-to-end after this sprint.",
        "",
        "## HARD RULES",
        f"- Build ONLY Sprint {num}: {title}. Nothing more, nothing less.",
        "- Do not rebuild Sprint 1 or any other previously completed sprint.",
        "- Do not regenerate the app from scratch.",
        "- Preserve all functionality completed in previous sprints.",
        "- Do not implement future sprints.",
        "- Do not rewrite unrelated files.",
        "- Keep the app runnable after this sprint.",
        "- Use the preserved sprint plan above exactly — do not redesign or reorder it.",
        "",
        "## AFTER YOU FINISH",
        "Print a clear summary of every file you created and every file you modified, and confirm "
        "you did not touch anything outside this sprint's scope.",
    ]

    prompt_text = "\n".join(parts)

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    scope_md = render_continuation_sprint_scope_markdown(next_sprint, previous_sprint, detected_mode, plan_json)
    (run_dir / "selected_continuation_sprint_scope.md").write_text(scope_md, encoding="utf-8")
    (run_dir / "selected_continuation_sprint_build_prompt.txt").write_text(prompt_text, encoding="utf-8")
    return prompt_text


def snapshot_app_for_continuation(app_path: Path, run_dir) -> dict:
    """Hashes every file in the COPIED app baseline BEFORE the continuation build,
    so run_continuation_regression_check can detect ANY unexpected change to
    previously-built functionality — not just an explicit must_not_modify list,
    which normal-mode sprint plans don't have. Writes continuation_baseline_hashes.json."""
    app_path = Path(app_path)
    hashes = {}
    for f in app_path.rglob("*"):
        if f.is_file() and not any(part in _SCAN_IGNORE_DIRS for part in f.parts):
            try:
                hashes[str(f.relative_to(app_path))] = _hash_file(f)
            except Exception:
                pass
    record = {"hashes": hashes}
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "continuation_baseline_hashes.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def run_continuation_regression_check(
    app_path: Path,
    run_dir,
    next_sprint: dict,
    detected_mode: str,
    smoke_log: str = "",
) -> tuple[str, str]:
    """
    Compares full-app file hashes from BEFORE the continuation build to AFTER.
    A pre-existing file is allowed to change only if this sprint explicitly
    declared it under likely_files_modified/must_not_modify context (i.e. the
    sprint plan said this sprint would touch it). PASS if nothing outside the
    declared scope changed/disappeared and expected new files exist; FAIL if a
    previously-completed-sprint file changed unexpectedly; UNKNOWN if there was
    no baseline to compare against. Writes continuation_regression_check.md.
    """
    run_dir = Path(run_dir)
    app_path = Path(app_path)
    baseline_path = run_dir / "continuation_baseline_hashes.json"
    must_not_modify = _sprint_must_not_modify(next_sprint, detected_mode)
    allowed_modified = set(_sprint_files_modified(next_sprint, detected_mode))

    def _is_allowed(rel: str) -> bool:
        return rel in allowed_modified or any(
            rel.startswith(a.rstrip("/") + "/") for a in allowed_modified
        )

    if not baseline_path.exists():
        status = "UNKNOWN"
        changed, unexpected_missing, baseline_hashes = [], [], {}
    else:
        baseline = _safe_read_json(baseline_path)
        baseline_hashes = baseline.get("hashes", {})
        changed, missing = [], []
        for rel, old_hash in baseline_hashes.items():
            p = app_path / rel
            if not p.exists():
                missing.append(rel)
                continue
            try:
                new_hash = _hash_file(p)
            except Exception:
                missing.append(rel)
                continue
            if new_hash != old_hash and not _is_allowed(rel):
                changed.append(rel)
        unexpected_missing = [m for m in missing if not _is_allowed(m)]
        status = "UNKNOWN" if not baseline_hashes else ("FAIL" if (changed or unexpected_missing) else "PASS")

    expected_new = [f for f in _sprint_files_created(next_sprint, detected_mode) if not any(ch in f for ch in "*?[")]
    confirmed_new = [f for f in expected_new if (app_path / f).exists()]
    missing_new = [f for f in expected_new if f not in confirmed_new]

    lines = ["# Continuation Regression Check", "", f"**Status:** {status}", ""]
    lines.append("## Files Tracked From Previous Baseline")
    lines.append(f"{len(baseline_hashes)} file(s) hashed before this sprint's build.")
    lines.append("")
    lines.append("## Unexpectedly Changed Files (outside this sprint's declared scope)")
    for f in changed or ["(none)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Unexpectedly Missing Files")
    for f in unexpected_missing or ["(none)"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Expected New Files For This Sprint")
    lines.append(f"Confirmed present: {len(confirmed_new)}/{len(expected_new)}")
    for f in missing_new:
        lines.append(f"- MISSING expected new file: {f}")
    lines.append("")
    if must_not_modify:
        lines.append("## Explicitly Protected Files / Areas For This Sprint")
        for f in must_not_modify:
            lines.append(f"- {f}")
        lines.append("")
    if smoke_log:
        lines.append("## Smoke / Runtime Check Results")
        lines.append("```")
        lines.append(smoke_log[:2000])
        lines.append("```")
        lines.append("")
    if status == "UNKNOWN":
        lines.append("## Note")
        lines.append(
            "No file-hash baseline was available to compare against. Regression confidence for "
            "this continuation sprint is limited to the expected-new-file and smoke checks above."
        )
    content = "\n".join(lines) + "\n"
    (run_dir / "continuation_regression_check.md").write_text(content, encoding="utf-8")
    return status, content


CONTINUATION_COMPLETION_REPORT_SYSTEM = """You are writing the handoff summary for ONE sprint that \
was just built as a CONTINUATION of an existing multi-sprint product. Be practical, honest, specific. \
Never describe this as a new product or as "Sprint 1" — state clearly which sprint number this was \
and that it builds on previously completed sprints.

Write in this exact format:

# Continuation Completion Report

## Source Run
Which previous run this continued from, and what mode it was.

## Sprint Built
Number and title of the sprint just built.

## What Previous Functionality Was Preserved
Bullet list, grounded in the regression check.

## What New Functionality Was Added
Bullet list of what this sprint actually added.

## Files Created / Modified / Deleted
Summarize from the build output and regression check.

## Regression Result
State the PASS/FAIL/UNKNOWN result and what it means.

## Smoke Check Result
State the result if available, or "Not run."

## Next Recommended Sprint
Name the next sprint from the preserved plan and why it comes next, or state this was the last \
planned sprint.

## Risks / TODOs
Bullet list. Be honest — include anything regression flagged.
"""


def generate_continuation_completion_report(
    source_info: dict,
    plan_json: dict,
    detected_mode: str,
    next_sprint: dict,
    build_output: str,
    regression_status: str,
    regression_report: str,
    smoke_log: str,
    run_dir,
) -> str:
    key = _sprint_key(detected_mode)
    all_sprints = _all_sprints(plan_json, detected_mode)
    n = next_sprint.get(key)
    upcoming = next((s for s in all_sprints if s.get(key, 0) == n + 1), None)
    upcoming_label = (
        f"Sprint {upcoming.get(key)}: {upcoming.get('title', '')}" if upcoming
        else "(none — this was the last planned sprint in the preserved plan)"
    )
    report = gpt([
        {"role": "system", "content": CONTINUATION_COMPLETION_REPORT_SYSTEM},
        {"role": "user", "content": (
            f"## SOURCE RUN\n{source_info['source_run']} (detected mode: {detected_mode})\n\n"
            f"## SPRINT BUILT\nSprint {n}: {next_sprint.get('title', '')}\n"
            f"Goal: {next_sprint.get('goal', '')}\n\n"
            f"## NEXT SPRINT (if any)\n{upcoming_label}\n\n"
            f"## CLAUDE CODE BUILD OUTPUT (tail)\n{build_output[-4000:]}\n\n"
            f"## REGRESSION STATUS: {regression_status}\n{regression_report}\n\n"
            f"## SMOKE LOG\n{smoke_log[:2000]}\n\n"
            "Write the continuation completion report."
        )},
    ])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "continuation_completion_report.md").write_text(report, encoding="utf-8")
    return report


def build_continuation_sprint(run_id: str, app_path: Path, build_prompt_text: str) -> str:
    """Runs Claude Code IN PLACE inside the COPIED app baseline for this
    continuation run (never the source run's folder). Same in-place build
    mechanics as build_feature_sprint."""
    full_prompt = (
        f"{build_prompt_text}\n\n"
        "---\n"
        "You are working inside a COPY of an existing multi-sprint application's directory. "
        "Do not create a new project at the top level. Add files relative to the existing "
        "structure you can already see here.\n"
        "After building, print a summary of every file you created and every file you modified.\n"
    )
    output = _stream_subprocess(
        CLAUDE_CODE_CMD + [full_prompt],
        cwd=str(app_path),
        timeout=CLAUDE_TIMEOUT,
    )
    save_artifact(run_id, "claude_build_output.txt", output)
    log_event(run_id, "claude_build_continuation_sprint", output[:500])
    return output


def pipeline_continue_sprint(
    continue_run: str,
    continue_sprint: int,
    continue_plan_only: bool = False,
    use_deepseek: bool = True,
    run_id: str | None = None,
) -> str:
    """
    Multi-Sprint Continuation Mode entry point. Loads (never regenerates) the
    preserved sprint plan from a previous run, copies that run's app baseline
    into a NEW run folder (never mutating the source run), builds ONLY the
    requested next sprint on top of it, runs a regression check against
    everything previously completed, and writes a continuation completion
    report. Works for both normal sprint-mode runs and Existing App Upgrade runs.
    """
    source_info = detect_continuation_source(Path(continue_run))
    detected_mode = source_info["detected_mode"]
    source_run_path = Path(source_info["source_run"])

    plan_json = load_preserved_sprint_plan(source_info)
    next_sprint = select_continuation_sprint(plan_json, detected_mode, continue_sprint)
    key = _sprint_key(detected_mode)
    previous_sprint = None
    prev_num = source_info.get("previous_selected_sprint")
    if prev_num:
        for s in plan_json.get("sprints", []):
            if s.get(key) == prev_num:
                previous_sprint = s
                break

    if not run_id:
        run_id = next_run_id()
    rdir = run_dir(run_id)
    init_run(run_id, f"Sprint Continuation — from {source_run_path} — Sprint {continue_sprint}")
    _update_state(run_id, {
        "mode": "continue_sprint",
        "source_run": str(source_run_path),
        "continue_sprint": continue_sprint,
        "source_mode": detected_mode,
        "status": "continuation_planning" if continue_plan_only else "continuation_building",
    })

    print(f"\n{'='*60}")
    print("  Multi-Sprint Continuation Mode")
    print(f"  Run folder    : {rdir}")
    print(f"  Source run    : {source_run_path}")
    print(f"  Detected mode : {detected_mode}")
    print(f"  Next sprint   : {continue_sprint}")
    print(f"  Plan only     : {continue_plan_only}")
    print(f"{'='*60}\n")

    print("▶ Step 1  Copying app baseline into new run folder (source run untouched)...")
    t0 = time.time()
    app_path = copy_baseline_app_for_continuation(source_info, rdir)
    record_step_time(run_id, "copy_baseline", t0)
    print(f"  App baseline copied to: {app_path}")

    print("▶ Step 2  Loading preserved sprint plan (not regenerated)...")
    preserved_plan_md = write_preserved_sprint_plan_artifacts(plan_json, detected_mode, rdir)

    print("▶ Step 3  Writing continuation source artifact...")
    write_continuation_source_artifact(source_info, continue_sprint, app_path, rdir)

    print("▶ Step 4  Scanning current app baseline...")
    t0 = time.time()
    current_scan, current_inventory_md = scan_current_app_for_continuation(app_path, rdir)
    record_step_time(run_id, "current_app_scan", t0)
    print(f"  Detected stack: {', '.join(current_scan['tech_stack'])}")

    print("▶ Step 5  Writing continuation gap analysis...")
    t0 = time.time()
    generate_continuation_gap_analysis(
        preserved_plan_md, previous_sprint, next_sprint, current_inventory_md, detected_mode, rdir,
    )
    record_step_time(run_id, "continuation_gap_analysis", t0)

    print(f"▶ Step 6  Writing selected continuation sprint scope + build prompt (Sprint {continue_sprint})...")
    build_prompt_text = generate_continuation_sprint_build_prompt(
        detected_mode, plan_json, next_sprint, previous_sprint, current_scan, rdir,
    )
    _update_state(run_id, {})

    if continue_plan_only:
        _update_state(run_id, {"status": "continuation_plan_only_done", "current_step": "done"})
        log_event(run_id, "continuation_plan_only_done")
        print(f"\n{'='*60}")
        print("  📝  Continuation plan-only run complete — planning artifacts generated. "
              "No Claude Code or DeepSeek calls made.")
        print(f"  Run folder : {rdir}")
        print(f"  Source run : {source_run_path} (untouched)")
        print("  Artifacts  :")
        for f in sorted(rdir.iterdir()):
            if f.is_file():
                print(f"        {f.name}")
        print(f"{'='*60}\n")
        return run_id

    print("▶ Step 7  Snapshotting app baseline before build...")
    snapshot_app_for_continuation(app_path, rdir)

    print(f"\n▶ Step 8  Claude Code — building Sprint {continue_sprint} on top of previous sprint(s)...")
    t0 = time.time()
    _update_state(run_id, {"current_step": "building", "status": "continuation_building"})
    build_output = build_continuation_sprint(run_id, app_path, build_prompt_text)
    record_step_time(run_id, "built", t0)
    _update_state(run_id, {"status": "built"})

    print("\n▶ Step 9  Continuation regression check...")
    smoke_log = ""
    try:
        smoke_log = run_smoke_checks(run_id, app_path)
    except Exception as e:
        smoke_log = f"Smoke checks could not run: {e}"
    save_artifact(run_id, "continuation_smoke_log.txt", smoke_log)
    regression_status, regression_report = run_continuation_regression_check(
        app_path, rdir, next_sprint, detected_mode, smoke_log,
    )
    print(f"  Regression result: {regression_status}")
    if regression_status == "FAIL":
        print("  ⚠️  WARNING: unexpected changes to previously-completed sprint files were detected.")

    if use_deepseek and DEEPSEEK_API_KEY:
        print("\n▶ Step 10  DeepSeek review (optional)...")
        deepseek_report = deepseek_attack_review(
            f"Continuation Sprint {continue_sprint} built on top of source run {source_run_path} "
            f"(detected mode: {detected_mode}).",
            app_path, smoke_log + "\n\n" + regression_report,
        )
        save_artifact(run_id, "deepseek_attack_report.md", deepseek_report)
        if not is_approved(deepseek_report):
            print("  Applying one fix iteration based on DeepSeek review...")
            fix_prompt = generate_fix_prompt(
                f"Continuation Sprint {continue_sprint}: {next_sprint.get('title', '')}",
                app_path, deepseek_report, 1,
            )
            apply_fixes(run_id, app_path, fix_prompt, 1)
            smoke_log = run_smoke_checks(run_id, app_path)
            regression_status, regression_report = run_continuation_regression_check(
                app_path, rdir, next_sprint, detected_mode, smoke_log,
            )
            print(f"  Regression result after fix: {regression_status}")

    print("\n▶ Step 11  Writing continuation completion report...")
    generate_continuation_completion_report(
        source_info, plan_json, detected_mode, next_sprint, build_output,
        regression_status, regression_report, smoke_log, rdir,
    )

    _update_state(run_id, {
        "status": "continuation_complete", "current_step": "done", "regression_status": regression_status,
    })
    log_event(run_id, "continuation_complete", f"regression={regression_status}")

    print(f"\n{'='*60}")
    print("  Multi-Sprint Continuation — Sprint Complete")
    print(f"  Run folder : {rdir}")
    print(f"  Source run : {source_run_path} (untouched)")
    print(f"  Regression : {regression_status}")
    print("  Artifacts  :")
    for f in sorted(rdir.iterdir()):
        if f.is_file():
            print(f"        {f.name}")
    print(f"{'='*60}\n")
    return run_id


# ── Step 3: Claude Code Build ──────────────────────────────────────────────────

def _stream_subprocess(cmd: list, cwd: str, timeout: int) -> str:
    """Run a command, printing each line live to stdout and collecting full output."""
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    lines = []
    try:
        for line in proc.stdout:
            stripped = line.rstrip("\n")
            print(stripped, flush=True)
            lines.append(stripped)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("  [TIMEOUT] Process killed after timeout", flush=True)
    return "\n".join(lines)


def build_mvp(run_id: str, build_prompt_text: str) -> Path:
    mvp_dir = run_dir(run_id) / "mvp"
    mvp_dir.mkdir(parents=True, exist_ok=True)

    full_prompt = (
        f"{build_prompt_text}\n\n"
        "---\n"
        "Save all files into the current directory.\n"
        "Create a complete, runnable local app.\n"
        "Write every file needed. Do not skip any file.\n"
        "After building, print a summary of what was created.\n"
    )

    output = _stream_subprocess(
        CLAUDE_CODE_CMD + [full_prompt],
        cwd=str(mvp_dir),
        timeout=CLAUDE_TIMEOUT,
    )
    save_artifact(run_id, "claude_build_output.txt", output)
    log_event(run_id, "claude_build", output[:500])
    return mvp_dir


# ── Step 4: Smoke Checks ──────────────────────────────────────────────────────

def run_smoke_checks(run_id: str, mvp_dir: Path) -> str:
    smoke_script = SMOKE_DIR / "run_smoke.sh"
    if not smoke_script.exists():
        return "ERROR: smoke_checks/run_smoke.sh not found. Skipping smoke checks."

    result = subprocess.run(
        ["bash", str(smoke_script), str(mvp_dir)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(mvp_dir),
    )
    log = (result.stdout + result.stderr).strip()
    return log if log else "Smoke checks produced no output."


def verify_architecture(
    run_id: str,
    mvp_dir: Path,
    spec: str,
    constraints: dict | None = None,
) -> str:
    """
    Static code analysis pass — catches shortcuts Claude takes to fake requirements.
    Checks the built code without needing a running server.

    Pass `constraints` (from detect_negative_constraints) so the function skips
    checks that are irrelevant for the app type.  Without constraints it falls back
    to a conservative keyword scan of the spec, but avoids bare terms like "api",
    "backend", and "database" because those words appear in negation phrases too
    ("no api", "no backend") and produce false positives on frontend-only apps.
    """
    lines = ["", "=" * 60, "  Architecture Verification (static)", "=" * 60]
    passed, failed = 0, 0

    c = constraints or {}

    # Derive flags from constraints when available; fall back to spec keywords.
    # IMPORTANT: the fallback scan must NOT match bare "api", "backend", "database"
    # since those terms appear in "no api", "no backend", "no database" too.
    if c:
        needs_db    = not (c.get("no_database") or c.get("no_backend") or c.get("frontend_only"))
        needs_api   = not (c.get("no_api")      or c.get("no_backend") or c.get("frontend_only"))
        no_api_mode = bool(c.get("no_api") or c.get("no_backend") or c.get("frontend_only"))
    else:
        spec_lower  = spec.lower()
        needs_db    = any(w in spec_lower for w in
                          ["postgresql", "postgres", "sqlite", "mysql", "mongodb"])
        needs_api   = any(w in spec_lower for w in
                          ["flask", "express", "fastapi", "django",
                           "rest api", "api endpoint", "backend api"])
        no_api_mode = False

    # ── Check 1: localStorage not used for persistence when DB required ─────────
    if needs_db:
        ls_hits = []
        for ext in ["*.ts", "*.tsx", "*.js", "*.jsx"]:
            r = subprocess.run(
                ["grep", "-r", "--include", ext, "-l",
                 "--exclude-dir=node_modules", "--exclude-dir=dist",
                 "--exclude-dir=build", "--exclude-dir=.git",
                 "localStorage"],
                cwd=str(mvp_dir), capture_output=True, text=True
            )
            ls_hits += [f for f in r.stdout.strip().splitlines()
                        if f and "node_modules" not in f and "/dist/" not in f]
        if ls_hits:
            lines.append("[FAIL] localStorage used for persistence when database was required:")
            for f in ls_hits:
                lines.append(f"       {f}")
            failed += 1
        else:
            lines.append("[PASS] No localStorage persistence found (database used as required)")
            passed += 1

    # ── Check 2: Backend files exist when API required ──────────────────────────
    if needs_api:
        backend_indicators = [
            "app.py", "server.py", "main.py",
            "backend/app.py", "backend/server.py",
            "server.js", "backend/server.js", "backend/index.js",
        ]
        found_backend = any((mvp_dir / f).exists() or
                            any(mvp_dir.rglob(Path(f).name)) for f in backend_indicators)
        if found_backend:
            lines.append("[PASS] Backend server file found")
            passed += 1
        else:
            lines.append("[FAIL] No backend server file found — API may not be implemented")
            failed += 1

    # ── Check 3: Frontend uses fetch/axios when API required ────────────────────
    if needs_api:
        fetch_hits = []
        for ext in ["*.ts", "*.tsx", "*.js", "*.jsx"]:
            r = subprocess.run(
                ["grep", "-r", "--include", ext, "-l",
                 "--exclude-dir=node_modules", "--exclude-dir=dist",
                 "--exclude-dir=build", r"fetch\|axios"],
                cwd=str(mvp_dir), capture_output=True, text=True
            )
            fetch_hits += [f for f in r.stdout.strip().splitlines() if f]
        if fetch_hits:
            lines.append("[PASS] Frontend uses fetch/axios for API calls")
            passed += 1
        else:
            lines.append("[FAIL] Frontend has no fetch/axios calls — may not be calling backend API")
            failed += 1

    # ── Check 4: Frontend-only — verify no unexpected backend API calls ─────────
    if no_api_mode:
        fetch_hits = []
        for ext in ["*.ts", "*.tsx", "*.js", "*.jsx"]:
            r = subprocess.run(
                ["grep", "-r", "--include", ext, "-l",
                 "--exclude-dir=node_modules", "--exclude-dir=dist",
                 "--exclude-dir=build", r"fetch\|axios"],
                cwd=str(mvp_dir), capture_output=True, text=True
            )
            fetch_hits += [f for f in r.stdout.strip().splitlines() if f]
        if fetch_hits:
            # Don't hard-fail — could be React internals or CDN fetch — flag as warning
            lines.append("[WARN] fetch/axios calls found in frontend — unexpected for no-API spec:")
            for f in fetch_hits:
                lines.append(f"       {f}")
        else:
            lines.append("[PASS] No fetch/axios calls found (correct for frontend-only app)")
            passed += 1

    # ── Check 5: Frontend source exists for frontend-only / no-backend apps ─────
    if no_api_mode or c.get("frontend_only"):
        src_candidates = ["src", "frontend/src", "client/src"]
        pkg_candidates = ["package.json", "frontend/package.json", "client/package.json"]
        has_frontend = (
            any((mvp_dir / p).is_dir() for p in src_candidates) or
            any((mvp_dir / p).exists() for p in pkg_candidates)
        )
        if has_frontend:
            lines.append("[PASS] Frontend source directory / package.json found")
            passed += 1
        else:
            lines.append("[FAIL] No frontend source directory or package.json found")
            failed += 1

    # ── Summary ─────────────────────────────────────────────────────────────────
    lines.append("")
    lines.append(f"  Architecture checks: {passed} passed, {failed} failed")
    if failed > 0:
        lines.append("  RESULT: ARCHITECTURE VIOLATIONS FOUND — review before approving")
    else:
        lines.append("  RESULT: Architecture looks correct")
    lines.append("=" * 60)

    result = "\n".join(lines)
    save_artifact(run_id, "architecture_check.txt", result)
    return result


# ── Step 5: DeepSeek Red-Team Review ─────────────────────────────────────────

DEEPSEEK_ATTACK_SYSTEM = """You are a senior engineer and product critic doing a red-team review of a locally built MVP.
You have been given the product spec, the file tree, key code excerpts, and smoke test results.

Your job: attack this build hard. Find every real problem that would prevent it from being a working MVP.

Review across these dimensions:

SPEC COMPLIANCE
- Does the MVP match the spec?
- Which key features are missing?
- Which acceptance criteria fail?

CODE QUALITY
- Obvious bugs that will crash the app
- Missing error handling on critical paths
- Hardcoded values that should be config
- Incomplete implementations (TODO stubs, placeholder logic)

UX / PRODUCT
- Would a real user actually be able to use this?
- Are there broken flows or dead ends?
- Is any required UI missing?

BACKEND / API
- Are all required endpoints implemented?
- Are inputs validated?
- Are there fragile assumptions?

DATABASE
- Is the schema correct for the spec?
- Are there missing indexes or constraints?

SECURITY (MVP-level)
- Obvious injection risks
- Exposed credentials in code
- Missing auth on routes that need it

SMOKE TEST ANALYSIS
- Review the smoke test results
- Call out any failed checks and what they mean

VERDICT
End with one of:
  VERDICT: APPROVED — no blockers, ship it
  VERDICT: FIX REQUIRED — list exactly what Claude Code must fix

Be specific. Quote file paths where relevant. Do not praise. Only report problems and required fixes.
"""

def collect_mvp_files(mvp_dir: Path, max_chars: int = 12000) -> str:
    collected = []
    total = 0
    for root, dirs, files in os.walk(mvp_dir):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "__pycache__", "venv", ".git", "dist", "build")]
        for fname in sorted(files):
            if any(fname.endswith(ext) for ext in CODE_EXTS):
                path = Path(root) / fname
                try:
                    content = path.read_text(encoding="utf-8")
                    rel = str(path.relative_to(mvp_dir))
                    entry = f"=== {rel} ===\n{content}\n"
                    if total + len(entry) > max_chars:
                        collected.append(f"=== {rel} === [TRUNCATED]")
                        break
                    collected.append(entry)
                    total += len(entry)
                except Exception:
                    pass
    return "\n".join(collected) if collected else "(no source files found)"


def file_tree(mvp_dir: Path) -> str:
    lines = []
    for root, dirs, files in os.walk(mvp_dir):
        dirs[:] = [d for d in sorted(dirs) if d not in ("node_modules", "__pycache__", "venv", ".git", "dist", "build")]
        depth = len(Path(root).relative_to(mvp_dir).parts)
        if depth > 0:
            lines.append(f"{'  ' * depth}{Path(root).name}/")
        for f in sorted(files):
            lines.append(f"{'  ' * (depth + 1)}{f}")
    return "\n".join(lines) if lines else "(empty)"


def deepseek_attack_review(spec: str, mvp_dir: Path, smoke_log: str) -> str:
    if not deepseek_client:
        return "DeepSeek API key not set. Skipping attack review.\nSet DEEPSEEK_API_KEY in config.py."

    user_msg = (
        f"## MVP SPEC\n{spec}\n\n"
        f"## FILE TREE\n{file_tree(mvp_dir)}\n\n"
        f"## SOURCE CODE\n{collect_mvp_files(mvp_dir)}\n\n"
        f"## SMOKE TEST LOG\n{smoke_log}\n\n"
        "Now do your red-team review."
    )

    try:
        resp = deepseek_client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": DEEPSEEK_ATTACK_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"DeepSeek API error: {e}"


# ── Step 6: Fix Prompt ────────────────────────────────────────────────────────

def generate_fix_prompt(
    spec: str,
    mvp_dir: Path,
    deepseek_report: str,
    fix_iteration: int,
    judged_report: str = "",
    consistency_report: str = "",
) -> str:
    """Build the Claude Code fix prompt for one fix iteration.

    When a judged_report is present (Phase 3), Claude Code is told to fix only
    CRITICAL and MAJOR issues from the judged report's Fix Scope section, and to
    ignore MINOR/NOISE issues.  When no judged_report is present (Phase 2 fallback),
    the raw DeepSeek report is used as before.
    """
    parts = [
        f"# Claude Code Fix Prompt — Iteration {fix_iteration}",
        "",
        f"## ORIGINAL MVP SPEC\n{spec}",
        "",
        f"## CURRENT FILE TREE\n{file_tree(mvp_dir)}",
        "",
    ]

    if judged_report:
        parts += [f"## JUDGED ISSUE REPORT\n{judged_report}", ""]
    else:
        parts += [f"## RED-TEAM ATTACK REPORT (DeepSeek)\n{deepseek_report}", ""]

    parts.append("## INSTRUCTIONS")
    if judged_report:
        parts.append(
            "Fix ONLY the CRITICAL and MAJOR issues listed in the Fix Scope section "
            "of the Judged Issue Report above."
        )
        parts.append(
            "Do NOT address MINOR or NOISE issues — they are not blockers and must not "
            "be touched to keep the diff minimal."
        )
    else:
        parts.append("Fix ONLY the issues listed in the attack report above.")

    if consistency_report:
        parts.append(
            "HARD CONSTRAINT — do not violate these explicit requirements exclusions "
            "(from the requirements consistency check):"
        )
        parts.append(consistency_report)

    parts += [
        "Do not refactor working code. Do not add features not in the spec.",
        "Preserve all working parts. Fix each listed issue one by one.",
        "After fixing, print a summary of every change made.",
    ]
    return "\n".join(parts)


def apply_fixes(run_id: str, mvp_dir: Path, fix_prompt: str, fix_iteration: int) -> str:
    output = _stream_subprocess(
        CLAUDE_CODE_CMD + [fix_prompt],
        cwd=str(mvp_dir),
        timeout=CLAUDE_TIMEOUT,
    )
    save_artifact(run_id, f"claude_fix_output_{fix_iteration}.txt", output)
    log_event(run_id, f"claude_fix_{fix_iteration}", output[:500])
    return output


# ── Step 8: Final Report ──────────────────────────────────────────────────────

FINAL_REPORT_SYSTEM = """You are writing the handoff summary for a completed MVP build.
Be practical and direct. Engineers and product managers read this.

Write the report in this exact format:

# MVP Build Report — <product name>

## What Was Built
2-3 sentences describing the app and its core function.

## How to Run It
Exact commands to start the app locally.

## Features That Work
Numbered list of working features from the spec.

## Known Issues / Missing Features
Numbered list. Be honest.

## Known Limitations
Numbered list of caveats.

## Recommended Next Steps
Top 3 improvements for V2.

## Final Status
One of:
  ✅ APPROVED — ready to demo
  ⚠️ PARTIAL — core works, some spec items missing
  ❌ BLOCKED — critical issues prevent basic use
"""

def generate_final_report(spec: str, mvp_dir: Path, fix_iterations_done: int, deepseek_report: str) -> str:
    return gpt([
        {"role": "system", "content": FINAL_REPORT_SYSTEM},
        {"role": "user", "content": (
            f"## MVP SPEC\n{spec}\n\n"
            f"## FILE TREE (final state)\n{file_tree(mvp_dir)}\n\n"
            f"## DEEPSEEK ATTACK REPORT (final)\n{deepseek_report}\n\n"
            f"Fix iterations completed: {fix_iterations_done}\n\n"
            "Write the final MVP build report."
        )},
    ])


# ── Approval check ────────────────────────────────────────────────────────────

def is_approved(deepseek_report: str) -> bool:
    return "VERDICT: APPROVED" in deepseek_report


# ── Phase 3: GPT-mini judgment of DeepSeek criticism ─────────────────────────

JUDGE_SYSTEM = """You are a senior engineering lead reviewing a red-team attack report on an MVP build.
Your job is to classify each issue raised by the attacker so the build team knows exactly
what to fix NOW vs. what to ignore.

Classification definitions:
- CRITICAL: The app cannot run, a core required feature is completely missing, smoke checks
  fail, there is a data loss or security problem, or an explicit user requirement is violated.
- MAJOR: The app runs but important required behaviour is wrong or incomplete.
- MINOR: Polish, small UX issue, wording, or a non-blocking improvement that does not break
  the stated requirements.
- NOISE: Speculative, irrelevant, contradicts the requirements, asks for out-of-scope work,
  or ignores explicit exclusions stated in the spec (e.g. complaining about a missing backend
  when the spec explicitly said "no backend", "frontend-only", or "no database").

Rules:
- If DeepSeek complains about something the spec explicitly excludes, classify that as NOISE.
- If an issue might matter in a real product but is outside MVP scope, classify as NOISE or MINOR.
- Only CRITICAL and MAJOR issues should be fixed now.

Output this EXACT structure (do not skip or rename any heading):

# Judged Issue Report

## Verdict
PASS or FIX_REQUIRED

(PASS means all issues are MINOR or NOISE.
FIX_REQUIRED means at least one CRITICAL or MAJOR issue exists.)

## Summary
1–2 sentences summarising the overall quality of the build.

## Issues

### Issue 1
- DeepSeek claim: <quote or paraphrase the original claim>
- Classification: CRITICAL / MAJOR / MINOR / NOISE
- Reason: <why you classified it this way>
- Should fix now: yes / no
- Fix instruction: <specific Claude Code instruction, or "none" if MINOR/NOISE>

(repeat the Issue block for each distinct issue)

## Fix Scope
Number each fix instruction for CRITICAL and MAJOR issues here.
If there are none, write exactly: None — build meets requirements.
"""


def _parse_verdict(judged_report: str) -> str:
    """Extract PASS or FIX_REQUIRED from a judged issue report.
    Looks for the verdict immediately after the '## Verdict' heading.
    Falls back to FIX_REQUIRED if the section is missing or unparseable
    (fail-safe: unknown state → treat as needs a fix).
    """
    lines = judged_report.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("## Verdict"):
            for j in range(i + 1, min(i + 6, len(lines))):
                candidate = lines[j].strip()
                if candidate in ("PASS", "FIX_REQUIRED"):
                    return candidate
    # Full-file fallback scan
    for line in lines:
        if line.strip() in ("PASS", "FIX_REQUIRED"):
            return line.strip()
    return "FIX_REQUIRED"  # fail-safe


def judged_report_requires_fix(report_text: str) -> bool:
    """Deterministic helper: True iff the judged report calls for a fix."""
    return _parse_verdict(report_text) == "FIX_REQUIRED"


def judge_deepseek_criticism(
    spec: str,
    architecture: str,
    build_prompt: str,
    smoke_log: str,
    deepseek_report: str,
    run_id: str,
    iteration: int,
) -> tuple[str, str]:
    """
    Call GPT-4o-mini to classify each DeepSeek issue as CRITICAL / MAJOR / MINOR / NOISE.
    Saves the judged report artifact and returns (verdict, report_text).
    verdict is 'PASS' or 'FIX_REQUIRED'.
    """
    user_msg = (
        f"## MVP SPEC\n{spec}\n\n"
        f"## ARCHITECTURE\n{architecture}\n\n"
        f"## BUILD PROMPT\n{build_prompt}\n\n"
        f"## SMOKE TEST LOG\n{smoke_log}\n\n"
        f"## DEEPSEEK ATTACK REPORT\n{deepseek_report}\n\n"
        "Now classify each issue in the attack report."
    )
    report = gpt([
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user_msg},
    ])
    suffix = "" if iteration == 1 else f"_{iteration}"
    save_artifact(run_id, f"judged_issue_report{suffix}.md", report)
    return _parse_verdict(report), report


# ── Governance Panel ──────────────────────────────────────────────────────────
# Three independent reviewers (AppSec, Legal/Privacy, Infra) feed a GPT-mini
# meta-judge that merges and classifies findings.  Runs after the MVP passes
# the quality loop, before the final report.  Skipped on --no-deepseek or
# --skip-governance because two of the three reviewers call DeepSeek.

GOVERNANCE_APPSEC_SYSTEM = """You are a senior application-security engineer performing an \
OWASP-aligned security review of a locally built MVP.

Your scope is strictly limited to what the build actually contains and what the requirements \
explicitly include.  Do NOT suggest adding security features (authentication, authorisation, \
rate-limiting, HTTPS, encryption) that the requirements explicitly exclude.

Review against:
- OWASP Top 10 (at MVP scale — ignore enterprise-only concerns)
- Hardcoded secrets or credentials in source files
- Exposed environment variables or .env files committed to source
- Client-side XSS risks (if frontend code exists)
- SQL injection / command injection (if backend + DB exist)
- Insecure direct object references (if user data routes exist)
- Missing input validation on any required API endpoint
- Use of vulnerable or deprecated library versions (if visible in package files)

Ignore:
- Missing TLS / HTTPS (local-only MVP, not deployed)
- Rate-limiting, DDOS protection, WAF (out of scope for local MVP)
- Any feature the requirements explicitly excluded

Output EXACTLY this structure:

# AppSec Governance Report

## Summary
1–2 sentences on the overall security posture.

## Findings

### Finding 1
- Issue: <describe the vulnerability>
- Severity: CRITICAL / MAJOR / MINOR / NOISE
- Location: <file name, line, or area>
- Recommendation: <specific fix, or "none required">

(repeat for each distinct finding; if none, write "No security findings.")

## Overall Assessment
PASS or CONCERNS
"""

GOVERNANCE_LEGAL_SYSTEM = """You are a senior legal and privacy compliance reviewer evaluating \
a locally built MVP before it is used.

Focus only on:
1. Third-party dependency licensing — scan package.json / requirements.txt for GPL / AGPL / \
   SSPL licences that could impose obligations on the author.
2. Data handling — if the app collects, stores, or transmits personal data (PII), flag GDPR / \
   CCPA / PIPEDA basics: notice, consent, retention.
3. Third-party API terms of service — if the code calls an external API, note whether \
   the usage appears within typical ToS limits.
4. Intellectual-property — obvious code copying or attribution issues.

Do NOT flag:
- Concerns about a deployed/production environment (this is a local MVP).
- Privacy issues for data the app explicitly does not collect or store.
- Features the requirements explicitly excluded.

Output EXACTLY this structure:

# Legal & Privacy Governance Report

## Summary
1–2 sentences on the overall legal/privacy posture.

## Findings

### Finding 1
- Issue: <describe the concern>
- Severity: CRITICAL / MAJOR / MINOR / NOISE
- Area: <dependency name, file, or data flow>
- Recommendation: <specific action, or "none required">

(repeat for each distinct finding; if none, write "No legal/privacy findings.")

## Overall Assessment
PASS or CONCERNS
"""

GOVERNANCE_INFRA_SYSTEM = """You are a senior DevOps and infrastructure engineer reviewing \
a locally built MVP for deployment and operational risk.

This MVP is LOCAL ONLY — do not suggest cloud infrastructure, containers, or CI/CD pipelines \
unless the requirements explicitly asked for them.

Review for:
- Hardcoded ports, hosts, or credentials that should be in .env / config
- Missing or incomplete .env.example
- Missing error handling in server startup (unhandled exceptions on boot)
- Build scripts that will silently fail (missing exit codes, unchecked commands)
- npm / pip dependency security issues visible in lock files or package files
- Port conflicts with common local services
- Secrets or API keys visible in source files

Do NOT suggest:
- Cloud deployment, Kubernetes, Docker (unless required)
- CI/CD pipelines (unless required)
- Monitoring, logging infrastructure (unless required)
- Any feature the requirements explicitly excluded

Output EXACTLY this structure:

# Infrastructure & Deployment Risk Report

## Summary
1–2 sentences on operational readiness.

## Findings

### Finding 1
- Issue: <describe the risk>
- Severity: CRITICAL / MAJOR / MINOR / NOISE
- Location: <file name or area>
- Recommendation: <specific fix, or "none required">

(repeat for each distinct finding; if none, write "No infrastructure findings.")

## Overall Assessment
PASS or CONCERNS
"""

GOVERNANCE_META_SYSTEM = """You are a governance committee chair reviewing three specialist \
reports (AppSec, Legal/Privacy, Infrastructure) on a locally built MVP.

Your job:
1. Merge and deduplicate findings across all three reports.
2. Classify each consolidated finding as CRITICAL / MAJOR / MINOR / NOISE.
3. Decide the governance verdict: PASS or FIX_REQUIRED.

Classification rules:
- CRITICAL: security vulnerability that could cause data loss, credential exposure, or \
  break a required feature; legal issue that creates direct liability risk.
- MAJOR: real problem that should be fixed but does not constitute an immediate emergency.
- MINOR: polish, cosmetic, or low-risk issue that does not block use.
- NOISE: speculative, contradicts the requirements, or concerns out-of-scope features.

Hard constraint: if the requirements explicitly EXCLUDE a feature (e.g. "no backend", \
"no database", "no auth"), do NOT classify complaints about the absence of that feature \
as CRITICAL or MAJOR — they are NOISE.  The REQUIREMENTS CONSISTENCY CHECK section in \
the context tells you what is excluded.

Verdict rule: PASS if all findings are MINOR or NOISE; FIX_REQUIRED if any are CRITICAL \
or MAJOR.

Output EXACTLY this structure (do not skip or rename any heading):

# Governance Meta-Judgment

## Verdict
PASS or FIX_REQUIRED

## Summary
1–2 sentences on the overall governance outcome.

## Consolidated Findings

### Issue 1
- Sources: AppSec / Legal / Infra / Multiple
- Finding: <what the issue is>
- Classification: CRITICAL / MAJOR / MINOR / NOISE
- Reason: <why this classification>
- Should fix now: yes / no
- Fix instruction: <specific Claude Code instruction, or "none" if MINOR/NOISE>

(repeat for each consolidated issue; deduplicate overlapping findings)

## Fix Scope
Number each fix instruction for CRITICAL and MAJOR issues only.
If there are none, write exactly: None — build meets governance requirements.
"""


def _read_artifact_file(rdir: Path, name: str) -> str:
    """Read a run artifact by name; return placeholder if missing."""
    p = rdir / name
    return p.read_text(encoding="utf-8") if p.exists() else "(not available)"


def _governance_shared_context(
    spec: str,
    architecture: str,
    build_prompt: str,
    consistency_report: str,
    smoke_log: str,
    deepseek_report: str,
    judged_report: str,
    mvp_dir: Path,
    run_id: str,
) -> str:
    """Shared preamble injected into every governance reviewer prompt."""
    rdir = run_dir(run_id)
    raw_input  = _read_artifact_file(rdir, "raw_input.md")
    clean_reqs = _read_artifact_file(rdir, "clean_requirements.md")

    parts = [
        f"## RAW INPUT\n{raw_input}",
        f"## CLEAN REQUIREMENTS\n{clean_reqs}",
        f"## MVP SPEC\n{spec}",
        f"## ARCHITECTURE\n{architecture}",
        f"## BUILD PROMPT (excerpt)\n{build_prompt[:3000]}",
        f"## REQUIREMENTS CONSISTENCY CHECK\n{consistency_report}",
        f"## LATEST SMOKE TEST LOG\n{smoke_log}",
    ]
    if deepseek_report and "disabled" not in deepseek_report.lower():
        parts.append(f"## QUALITY ATTACK REPORT (DeepSeek)\n{deepseek_report[:3000]}")
    if judged_report:
        parts.append(f"## JUDGED QUALITY REPORT\n{judged_report[:2000]}")
    parts.append(f"## FILE TREE\n{file_tree(mvp_dir)}")
    parts.append(f"## SOURCE CODE\n{collect_mvp_files(mvp_dir)}")
    return "\n\n".join(parts)


def _governance_suffix(iteration: int) -> str:
    """Artifact filename suffix: '' for iteration 1, '_2' for 2, etc."""
    return "" if iteration == 1 else f"_{iteration}"


def run_governance_appsec(
    spec: str, architecture: str, build_prompt: str,
    consistency_report: str, smoke_log: str,
    deepseek_report: str, judged_report: str,
    mvp_dir: Path, run_id: str, iteration: int,
) -> str:
    """DeepSeek AppSec / OWASP review. Saves artifact, returns report text."""
    context = _governance_shared_context(
        spec, architecture, build_prompt, consistency_report,
        smoke_log, deepseek_report, judged_report, mvp_dir, run_id,
    )
    report = deepseek_chat([
        {"role": "system", "content": GOVERNANCE_APPSEC_SYSTEM},
        {"role": "user", "content": f"{context}\n\nNow perform your AppSec review."},
    ])
    fname = f"governance_appsec_report{_governance_suffix(iteration)}.md"
    save_artifact(run_id, fname, report)
    return report


def run_governance_legal_privacy(
    spec: str, architecture: str, build_prompt: str,
    consistency_report: str,
    mvp_dir: Path, run_id: str, iteration: int,
) -> str:
    """GPT-4o Legal / privacy / licensing review. Saves artifact, returns report text."""
    context = _governance_shared_context(
        spec, architecture, build_prompt, consistency_report,
        "(not applicable for legal review)", "", "", mvp_dir, run_id,
    )
    report = gpt4o([
        {"role": "system", "content": GOVERNANCE_LEGAL_SYSTEM},
        {"role": "user", "content": f"{context}\n\nNow perform your legal and privacy review."},
    ])
    fname = f"governance_legal_privacy_report{_governance_suffix(iteration)}.md"
    save_artifact(run_id, fname, report)
    return report


def run_governance_infra(
    spec: str, architecture: str, build_prompt: str,
    consistency_report: str,
    mvp_dir: Path, run_id: str, iteration: int,
) -> str:
    """DeepSeek infrastructure / deployment risk review. Saves artifact, returns report text."""
    context = _governance_shared_context(
        spec, architecture, build_prompt, consistency_report,
        "(not applicable for infra review)", "", "", mvp_dir, run_id,
    )
    report = deepseek_chat([
        {"role": "system", "content": GOVERNANCE_INFRA_SYSTEM},
        {"role": "user", "content": f"{context}\n\nNow perform your infrastructure risk review."},
    ])
    fname = f"governance_infra_report{_governance_suffix(iteration)}.md"
    save_artifact(run_id, fname, report)
    return report


def judge_governance_reports(
    appsec_report: str,
    legal_report: str,
    infra_report: str,
    spec: str,
    consistency_report: str,
    run_id: str,
    iteration: int,
) -> tuple[str, str]:
    """
    GPT-4o-mini meta-judge: merges and classifies all three governance reports.
    Returns (verdict, meta_report_text).  verdict is 'PASS' or 'FIX_REQUIRED'.
    Saves the meta-judgment artifact.
    """
    user_msg = (
        f"## MVP SPEC\n{spec}\n\n"
        f"## REQUIREMENTS CONSISTENCY CHECK\n{consistency_report}\n\n"
        f"## APPSEC REPORT\n{appsec_report}\n\n"
        f"## LEGAL & PRIVACY REPORT\n{legal_report}\n\n"
        f"## INFRASTRUCTURE REPORT\n{infra_report}\n\n"
        "Now produce the governance meta-judgment."
    )
    meta = gpt([
        {"role": "system", "content": GOVERNANCE_META_SYSTEM},
        {"role": "user", "content": user_msg},
    ])
    fname = f"governance_meta_judgment{_governance_suffix(iteration)}.md"
    save_artifact(run_id, fname, meta)
    return _parse_governance_verdict(meta), meta


def _parse_governance_verdict(meta_report: str) -> str:
    """
    Extract PASS or FIX_REQUIRED from a governance meta-judgment report.
    Scans the ## Verdict section first, then falls back to a full-file scan.
    Defaults to FIX_REQUIRED if unparseable (fail-safe).
    """
    lines = meta_report.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("## Verdict"):
            for j in range(i + 1, min(i + 6, len(lines))):
                candidate = lines[j].strip()
                if candidate in ("PASS", "FIX_REQUIRED"):
                    return candidate
    for line in lines:
        if line.strip() in ("PASS", "FIX_REQUIRED"):
            return line.strip()
    return "FIX_REQUIRED"  # fail-safe


def governance_requires_fix(meta_report: str) -> bool:
    """Deterministic helper: True iff the governance meta-judgment calls for a fix."""
    return _parse_governance_verdict(meta_report) == "FIX_REQUIRED"


def generate_governance_fix_prompt(
    spec: str,
    mvp_dir: Path,
    meta_report: str,
    consistency_report: str,
    iteration: int,
) -> str:
    """Build the Claude Code fix prompt for one governance fix iteration."""
    parts = [
        f"# Governance Fix Prompt — Iteration {iteration}",
        "",
        f"## ORIGINAL MVP SPEC\n{spec}",
        "",
        f"## CURRENT FILE TREE\n{file_tree(mvp_dir)}",
        "",
        f"## GOVERNANCE META-JUDGMENT\n{meta_report}",
        "",
        "## INSTRUCTIONS",
        "Fix ONLY the CRITICAL and MAJOR governance issues listed in the Fix Scope "
        "section of the Governance Meta-Judgment above.",
        "Do NOT address MINOR or NOISE governance issues.",
        "Do not add new features, expand scope beyond the MVP spec, or refactor "
        "working code that is unrelated to the governance issues.",
        "Preserve all working parts.",
    ]
    if consistency_report:
        parts += [
            "",
            "HARD CONSTRAINT — do not violate these explicit requirements exclusions:",
            consistency_report,
        ]
    parts += [
        "",
        "After fixing, print a summary of every governance-related change made.",
    ]
    return "\n".join(parts)


# ── Auto-launch MVP ───────────────────────────────────────────────────────────

def _auto_launch_mvp(mvp_dir: Path):
    """Install deps, start the MVP server, and open the browser."""
    import webbrowser

    # Find the actual project root (Claude sometimes creates a named subfolder)
    project_root = mvp_dir
    for child in mvp_dir.iterdir():
        if child.is_dir() and (child / "package.json").exists():
            project_root = child
            break
        if child.is_dir() and (child / "app.py").exists():
            project_root = child
            break

    print(f"\n  Launching MVP at {project_root} …")

    # Detect project type
    has_pkg   = (project_root / "package.json").exists()
    has_flask = (project_root / "app.py").exists() or (project_root / "backend" / "app.py").exists()

    def _launch():
        try:
            if has_pkg:
                env = os.environ.copy()
                env["NODE_OPTIONS"] = "--openssl-legacy-provider"

                # Install deps in root and any frontend/backend subdirs
                print(f"  Installing deps …")
                pkg = json.loads((project_root / "package.json").read_text())
                scripts = pkg.get("scripts", {})
                if "install-all" in scripts:
                    subprocess.run(["npm", "run", "install-all"],
                                   cwd=str(project_root), capture_output=True, env=env)
                else:
                    subprocess.run(["npm", "install"],
                                   cwd=str(project_root), capture_output=True, env=env)
                for sub in ["frontend", "backend"]:
                    sub_path = project_root / sub
                    if sub_path.exists() and (sub_path / "package.json").exists():
                        subprocess.run(["npm", "install"], cwd=str(sub_path),
                                       capture_output=True, env=env)

                # Pick start command and detect port
                # Prefer: dev > start (Vite projects use "dev", CRA uses "start")
                if "dev" in scripts:
                    start_cmd = ["npm", "run", "dev"]
                    port = 5173  # Vite default
                else:
                    start_cmd = ["npm", "start"]
                    port = 3000  # CRA default

                # Check if there's a frontend subdir with its own dev server
                fe_pkg_path = project_root / "frontend" / "package.json"
                if fe_pkg_path.exists():
                    fe_scripts = json.loads(fe_pkg_path.read_text()).get("scripts", {})
                    if "dev" in fe_scripts:
                        port = 5173
                    elif "start" in fe_scripts:
                        # Check for explicit PORT= in script
                        env_port = fe_scripts.get("start", "")
                        if "PORT=" in env_port:
                            try:
                                port = int(env_port.split("PORT=")[1].split()[0])
                            except Exception:
                                pass

                print(f"  Starting MVP server (cmd: {' '.join(start_cmd)}) …")
                subprocess.Popen(start_cmd, cwd=str(project_root),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=env)

            elif has_flask:
                print(f"  Starting Flask server …")
                app_path = project_root / "app.py"
                if not app_path.exists():
                    app_path = project_root / "backend" / "app.py"
                subprocess.Popen(
                    [sys.executable, str(app_path)],
                    cwd=str(app_path.parent),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                port = 5000
            else:
                print("  Could not detect project type — skipping auto-launch.")
                return

            # Wait for server to come up, then open browser
            url = f"http://localhost:{port}"
            print(f"  Opening {url} in browser in 8s …")
            time.sleep(8)
            webbrowser.open(url)
            print(f"  Browser opened: {url}")

        except Exception as e:
            print(f"  Auto-launch failed: {e}")

    threading.Thread(target=_launch, daemon=True).start()


# ── Main pipeline ─────────────────────────────────────────────────────────────

def _is_plan_only_run(plan_only: bool, sprint_plan_only: bool) -> bool:
    """Deterministic gate: True if this run must stop before any Claude Code or
    DeepSeek call. Used by both --plan-only and --sprint-plan-only."""
    return bool(plan_only or sprint_plan_only)


def pipeline(
    raw_input: str,
    run_id: str | None = None,
    use_deepseek: bool = True,
    resume: bool = False,
    mode: str | None = None,
    jira_used: bool = False,
    plan_only: bool = False,
    skip_governance: bool = False,
    sprint_plan: bool = False,
    selected_sprint: int = 1,
    sprint_plan_only: bool = False,
):
    if not run_id:
        run_id = next_run_id()

    if not resume:
        init_run(run_id, raw_input)

    rdir = run_dir(run_id)
    pipeline_start = time.time()

    sprint_mode_active = sprint_plan or sprint_plan_only
    _gov_active = use_deepseek and not skip_governance and not _is_plan_only_run(plan_only, sprint_plan_only)
    if plan_only:
        build_step_status = "not being run (plan-only)"
    elif sprint_plan_only:
        build_step_status = "not being run (sprint-plan-only)"
    else:
        build_step_status = f"Sprint {selected_sprint}" if sprint_mode_active else "full MVP"
    print(f"\n{'='*60}")
    print(f"  MVP Pipeline — {run_id}")
    print(f"  Run folder  : {rdir}")
    print(f"  DeepSeek    : {'enabled' if use_deepseek and DEEPSEEK_API_KEY else 'disabled (no key)'}")
    print(f"  Governance  : {'enabled' if _gov_active else 'skipped'}")
    print(f"  Sprint mode : {f'enabled (selected Sprint {selected_sprint})' if sprint_mode_active else 'disabled'}")
    print(f"  Build step  : {build_step_status}")
    print(f"{'='*60}\n")

    # ── Step 0: Planning Artifacts ───────────────────────────────────────────
    input_mode = detect_mode(raw_input, jira_used=jira_used, override=mode)
    print(f"▶ Step 0a  Mode detection — {input_mode}")
    _update_state(run_id, {"current_step": "mode_detection", "input_mode": input_mode})
    log_event(run_id, "mode_detected", input_mode)

    if input_mode == "idea":
        print("▶ Step 0b  GPT-mini — defining MVP scope (PM agent)")
        t0 = time.time()
        progress.start("GPT-mini", "defining MVP scope")
        mvp_scope = generate_mvp_scope(raw_input)
        save_artifact(run_id, "mvp_scope.md", mvp_scope)
        record_step_time(run_id, "mvp_scope", t0)
        progress.done("MVP scope written")
        requirements_source = mvp_scope
    else:
        requirements_source = raw_input

    print("▶ Step 0c  GPT-mini — normalizing clean requirements")
    t0 = time.time()
    progress.start("GPT-mini", "normalizing requirements")
    clean_requirements = normalize_requirements(requirements_source)
    save_artifact(run_id, "clean_requirements.md", clean_requirements)
    record_step_time(run_id, "clean_requirements", t0)
    progress.done("Clean requirements written")

    constraints = detect_negative_constraints(
        raw_input,
        mvp_scope if input_mode == "idea" else "",
        clean_requirements,
    )
    active_constraints = [k for k, v in constraints.items() if v]
    if active_constraints:
        print(f"  Negative constraints detected: {', '.join(active_constraints)}")
    log_event(run_id, "constraints_detected", ", ".join(active_constraints) or "none")

    # ── Step 1: MVP Spec ─────────────────────────────────────────────────────
    print("▶ Step 1/8  GPT-mini — writing MVP spec")
    t0 = time.time()
    _update_state(run_id, {"current_step": "spec"})
    progress.start("GPT-mini", "writing MVP spec")
    spec = generate_mvp_spec(clean_requirements, constraints)
    save_artifact(run_id, "mvp_spec.md", spec)
    record_step_time(run_id, "spec", t0)
    log_event(run_id, "spec_ready")
    progress.done("MVP spec written")
    print(f"\n{spec[:400]}{'...' if len(spec) > 400 else ''}\n")

    # ── Step 1d: Architecture contract ───────────────────────────────────────
    print("▶ Step 1d  GPT-mini — writing ARCHITECTURE.md")
    t0 = time.time()
    progress.start("GPT-mini", "writing architecture contract")
    architecture_text = generate_architecture(spec, constraints)
    save_artifact(run_id, "ARCHITECTURE.md", architecture_text)
    record_step_time(run_id, "architecture", t0)
    progress.done("ARCHITECTURE.md written")

    contract_ok, contract_report = check_architecture_contract(architecture_text)
    save_artifact(run_id, "architecture_contract_check.txt", contract_report)
    log_event(run_id, "architecture_contract", "ok" if contract_ok else "violations")
    if not contract_ok:
        print(f"  ⚠️  Architecture contract has violations — see architecture_contract_check.txt")
    print(f"  {contract_report[:400]}{'...' if len(contract_report) > 400 else ''}")

    smoke_checks_doc = generate_smoke_checks_doc(spec, architecture_text, constraints)
    save_artifact(run_id, "smoke_checks.md", smoke_checks_doc)

    # ── Step 2: Build Prompt ─────────────────────────────────────────────────
    print("▶ Step 2/8  GPT-mini — writing Claude Code build prompt")
    t0 = time.time()
    _update_state(run_id, {"current_step": "build_prompt", "status": "spec_ready"})
    progress.start("GPT-mini", "writing build prompt")
    build_prompt_text = generate_build_prompt(spec, constraints)
    save_artifact(run_id, "build_prompt.txt", build_prompt_text)
    record_step_time(run_id, "build_prompt", t0)
    log_event(run_id, "build_prompt_ready")
    progress.done("Build prompt written")

    # ── Step 2c: Sprint Decomposition (only when --sprint-plan / --sprint-plan-only) ──
    sprint_plan_json = None
    selected_sprint_entry = None
    selected_sprint_build_prompt_text = None

    if sprint_mode_active:
        print(f"\n▶ Step 2c  GPT-4o — decomposing MVP into sprints (architect)")
        t0 = time.time()
        _update_state(run_id, {"current_step": "sprint_plan"})
        progress.start("GPT-4o", "decomposing MVP into sprints")
        sprint_plan_json, _sprint_plan_md = generate_sprint_plan(
            clean_requirements, spec, architecture_text, constraints, rdir,
            selected_sprint_number=selected_sprint,
        )
        record_step_time(run_id, "sprint_plan", t0)
        _update_state(run_id, {})  # refresh artifacts list (sprint_plan.* written directly to rdir)
        progress.done("Sprint plan generated")

        selected_sprint_entry = select_sprint(sprint_plan_json, selected_sprint)

        terminal_plan = render_sprint_plan_terminal(sprint_plan_json, selected_sprint)
        print(f"\n{terminal_plan}\n")
        log_event(
            run_id, "sprint_plan_ready",
            f"total={sprint_plan_json.get('total_sprints')}, selected={selected_sprint}",
        )

        print(f"▶ Step 2d  Generating selected-sprint scope + build prompt (Sprint {selected_sprint})")
        selected_sprint_build_prompt_text = generate_selected_sprint_build_prompt(
            clean_requirements, spec, architecture_text,
            sprint_plan_json, selected_sprint_entry, constraints, rdir,
        )
        _update_state(run_id, {})  # refresh artifacts list
        log_event(run_id, "selected_sprint_build_prompt_ready", f"sprint={selected_sprint}")

    # ── Step 2b: Requirements consistency check ──────────────────────────────
    print("▶ Step 2b  Requirements consistency check (rule-based)")
    consistency_artifacts = {
        "mvp_spec.md": spec,
        "ARCHITECTURE.md": architecture_text,
        "smoke_checks.md": smoke_checks_doc,
        "build_prompt.txt": build_prompt_text,
    }
    if selected_sprint_build_prompt_text:
        consistency_artifacts["selected_sprint_build_prompt.txt"] = selected_sprint_build_prompt_text
    consistency_ok, consistency_report = check_requirements_consistency(constraints, consistency_artifacts)
    save_artifact(run_id, "requirements_consistency_check.txt", consistency_report)
    log_event(run_id, "requirements_consistency", "ok" if consistency_ok else "violations")
    print(f"  {consistency_report}")

    if not consistency_ok:
        _update_state(run_id, {"status": "blocked_consistency_violation", "current_step": "blocked"})
        print(f"\n{'='*60}")
        print(f"  BLOCKED — generated planning artifacts violate explicit requirements")
        print(f"  Run folder : {rdir}")
        print(f"  See requirements_consistency_check.txt for details")
        print(f"{'='*60}\n")
        raise RequirementsConsistencyError(consistency_report)

    if _is_plan_only_run(plan_only, sprint_plan_only):
        status = "sprint_plan_only_done" if sprint_plan_only else "plan_only_done"
        _update_state(run_id, {"status": status, "current_step": "done"})
        log_event(run_id, status)
        print(f"\n{'='*60}")
        if sprint_plan_only:
            print(f"  📝  Sprint-plan-only run complete — sprint plan + Sprint {selected_sprint} "
                  f"build prompt generated. No Claude Code or DeepSeek calls made.")
        else:
            print(f"  📝  Plan-only run complete — no Claude Code or DeepSeek calls made")
        print(f"  Run folder : {rdir}")
        print(f"  Artifacts  :")
        for f in sorted(rdir.iterdir()):
            if f.is_file():
                print(f"        {f.name}")
        print(f"{'='*60}\n")
        return run_id

    # ── Step 3: Claude Code Build ────────────────────────────────────────────
    build_text_for_claude = selected_sprint_build_prompt_text if sprint_mode_active else build_prompt_text
    print(f"▶ Step 3/8  Claude Code — building MVP"
          f"{f' (Sprint {selected_sprint} only)' if sprint_mode_active else ''}")
    t0 = time.time()
    _update_state(run_id, {"current_step": "building", "status": "building"})
    progress.start("Claude Code", "building MVP")
    mvp_dir = build_mvp(run_id, build_text_for_claude)
    record_step_time(run_id, "built", t0)
    _update_state(run_id, {"status": "built", "mvp_dir": str(mvp_dir)})
    progress.done("Initial MVP build complete")

    # ── Fix loop ─────────────────────────────────────────────────────────────
    fix_iteration = 0
    deepseek_report = "(DeepSeek review not yet run)"

    for cycle in range(MAX_FIX_ITERATIONS):
        cycle_label = f"{cycle + 1}/{MAX_FIX_ITERATIONS}"

        # Step 4: Smoke Checks + Architecture Verification
        print(f"\n▶ Step 4  Smoke checks  [cycle {cycle_label}]")
        t0 = time.time()
        _update_state(run_id, {"current_step": f"smoke_{cycle + 1}"})
        progress.start("Smoke checks", "running checks + architecture verification")
        smoke_log = run_smoke_checks(run_id, mvp_dir)
        arch_log  = verify_architecture(run_id, mvp_dir, spec, constraints)
        combined_log = smoke_log + "\n\n" + arch_log
        record_step_time(run_id, f"smoke_{cycle + 1}", t0)
        fname = f"smoke_test_log_{cycle + 1}.txt" if cycle > 0 else "smoke_test_log.txt"
        save_artifact(run_id, fname, combined_log)
        progress.done("Smoke checks + architecture verification complete")
        print(f"  {arch_log[:400]}{'...' if len(arch_log) > 400 else ''}")
        smoke_log = combined_log  # pass full log to DeepSeek

        # Step 5: DeepSeek
        if use_deepseek:
            print(f"\n▶ Step 5  DeepSeek red-team review  [cycle {cycle_label}]")
            t0 = time.time()
            _update_state(run_id, {"current_step": f"deepseek_{cycle + 1}"})
            progress.start("DeepSeek", "attacking the MVP")
            deepseek_report = deepseek_attack_review(spec, mvp_dir, smoke_log)
            record_step_time(run_id, f"deepseek_{cycle + 1}", t0)
            fname = f"deepseek_attack_report_{cycle + 1}.md" if cycle > 0 else "deepseek_attack_report.md"
            save_artifact(run_id, fname, deepseek_report)
            progress.done("DeepSeek review complete")
        else:
            deepseek_report = "DeepSeek disabled."

        # Step 5b: GPT-mini judges the DeepSeek criticism (Phase 3)
        # Only runs when DeepSeek actually produced a report (not disabled / key missing).
        judged_verdict = "PASS" if not use_deepseek else "FIX_REQUIRED"
        judged_report_text = ""
        _ds_ran = use_deepseek and "disabled" not in deepseek_report.lower() and "API key not set" not in deepseek_report
        if _ds_ran:
            judge_iter = cycle + 1
            print(f"\n▶ Step 5b  GPT-mini — judging DeepSeek criticism  [cycle {cycle_label}]")
            t0 = time.time()
            _update_state(run_id, {"current_step": f"judge_{judge_iter}"})
            progress.start("GPT-mini", "judging DeepSeek criticism")
            judged_verdict, judged_report_text = judge_deepseek_criticism(
                spec, architecture_text, build_prompt_text,
                smoke_log, deepseek_report, run_id, judge_iter,
            )
            record_step_time(run_id, f"judge_{judge_iter}", t0)
            progress.done(f"Judged verdict: {judged_verdict}")
            log_event(run_id, f"judge_{judge_iter}", judged_verdict)
            print(f"  🧠  Judged verdict: {judged_verdict}")

        # Check approval
        deepseek_approved = is_approved(deepseek_report)
        gpt_judged_pass   = _ds_ran and judged_verdict == "PASS"
        if deepseek_approved or gpt_judged_pass or (not use_deepseek):
            if deepseek_approved:
                reason = "DeepSeek VERDICT: APPROVED"
            elif gpt_judged_pass:
                reason = "GPT-mini judged all issues MINOR/NOISE — no fix needed"
            else:
                reason = "DeepSeek disabled"
            print(f"\n  MVP approved on cycle {cycle + 1} — {reason}")
            _update_state(run_id, {"status": "approved"})
            log_event(run_id, "approved", f"cycle={cycle + 1}, reason={reason}")
            break

        if cycle == MAX_FIX_ITERATIONS - 1:
            print(f"\n  ⚠️  Max fix iterations ({MAX_FIX_ITERATIONS}) reached.")
            _update_state(run_id, {"status": "max_iterations_reached"})
            log_event(run_id, "max_iterations_reached")
            break

        # Step 6+7: Fix prompt + Claude Code fixes
        fix_iteration += 1
        print(f"\n▶ Step 6  Generating fix prompt  [fix {fix_iteration}]")
        fix_prompt = generate_fix_prompt(
            spec, mvp_dir, deepseek_report, fix_iteration,
            judged_report=judged_report_text,
            consistency_report=consistency_report,
        )
        save_artifact(run_id, f"claude_fix_prompt_{fix_iteration}.md", fix_prompt)

        print(f"\n▶ Step 7  Claude Code — applying fixes  [fix {fix_iteration}]")
        t0 = time.time()
        _update_state(run_id, {"fix_iteration": fix_iteration, "current_step": f"fix_{fix_iteration}"})
        progress.start("Claude Code", f"fixing issues (iteration {fix_iteration})")
        apply_fixes(run_id, mvp_dir, fix_prompt, fix_iteration)
        record_step_time(run_id, f"fix_{fix_iteration}", t0)
        progress.done(f"Fix {fix_iteration} applied")

    # ── Governance Panel ─────────────────────────────────────────────────────
    # Runs after the quality loop approves the build, before the final report.
    # Skipped when --no-deepseek or --skip-governance is set (two of three
    # reviewers call DeepSeek).
    gov_verdict      = "PASS"
    gov_meta_report  = ""
    gov_fix_count    = 0
    gov_smoke_log    = smoke_log  # start with the latest smoke log from quality loop

    if _gov_active:
        print(f"\n{'─'*60}")
        print(f"  Governance Panel")
        print(f"{'─'*60}")
        _update_state(run_id, {"current_step": "governance"})

        def _run_governance_cycle(g_iter: int, g_smoke: str) -> tuple[str, str, str, str, str]:
            """Run all three reviewers + meta-judge for one governance cycle.
            Returns (appsec, legal, infra, verdict, meta_report)."""
            t0 = time.time()
            print(f"\n▶ Governance G1  AppSec / OWASP review  [gov cycle {g_iter}]")
            progress.start("DeepSeek", "AppSec / OWASP review")
            _appsec = run_governance_appsec(
                spec, architecture_text, build_prompt_text,
                consistency_report, g_smoke,
                deepseek_report, judged_report_text,
                mvp_dir, run_id, g_iter,
            )
            record_step_time(run_id, f"gov_appsec_{g_iter}", t0)
            progress.done("AppSec review complete")

            t0 = time.time()
            print(f"\n▶ Governance G2  Legal / privacy / licensing review  [gov cycle {g_iter}]")
            progress.start("GPT-4o", "legal / privacy review")
            _legal = run_governance_legal_privacy(
                spec, architecture_text, build_prompt_text,
                consistency_report, mvp_dir, run_id, g_iter,
            )
            record_step_time(run_id, f"gov_legal_{g_iter}", t0)
            progress.done("Legal review complete")

            t0 = time.time()
            print(f"\n▶ Governance G3  Infrastructure / deployment risk review  [gov cycle {g_iter}]")
            progress.start("DeepSeek", "infra risk review")
            _infra = run_governance_infra(
                spec, architecture_text, build_prompt_text,
                consistency_report, mvp_dir, run_id, g_iter,
            )
            record_step_time(run_id, f"gov_infra_{g_iter}", t0)
            progress.done("Infra review complete")

            t0 = time.time()
            print(f"\n▶ Governance G4  GPT-mini — governance meta-judgment  [gov cycle {g_iter}]")
            progress.start("GPT-mini", "governance meta-judgment")
            _verdict, _meta = judge_governance_reports(
                _appsec, _legal, _infra,
                spec, consistency_report, run_id, g_iter,
            )
            record_step_time(run_id, f"gov_meta_{g_iter}", t0)
            progress.done(f"Governance verdict: {_verdict}")
            log_event(run_id, f"gov_verdict_{g_iter}", _verdict)
            print(f"  Governance verdict: {_verdict}")
            return _appsec, _legal, _infra, _verdict, _meta

        # Initial governance review (iteration 1)
        _, _, _, gov_verdict, gov_meta_report = _run_governance_cycle(1, gov_smoke_log)

        # Fix loop — capped at MAX_GOVERNANCE_ITERATIONS
        while governance_requires_fix(gov_meta_report) and gov_fix_count < MAX_GOVERNANCE_ITERATIONS:
            gov_fix_count += 1
            suffix = _governance_suffix(gov_fix_count)

            print(f"\n▶ Governance G5  Generating governance fix prompt  [fix {gov_fix_count}]")
            gov_fix_prompt = generate_governance_fix_prompt(
                spec, mvp_dir, gov_meta_report, consistency_report, gov_fix_count,
            )
            save_artifact(run_id, f"governance_fix_prompt{suffix}.md", gov_fix_prompt)

            print(f"\n▶ Governance G6  Claude Code — applying governance fixes  [fix {gov_fix_count}]")
            t0 = time.time()
            _update_state(run_id, {"current_step": f"gov_fix_{gov_fix_count}"})
            progress.start("Claude Code", f"governance fixes (iteration {gov_fix_count})")
            apply_fixes(run_id, mvp_dir, gov_fix_prompt, f"gov_{gov_fix_count}")
            record_step_time(run_id, f"gov_fix_{gov_fix_count}", t0)
            progress.done(f"Governance fix {gov_fix_count} applied")

            print(f"\n▶ Governance G7  Smoke checks after governance fix  [fix {gov_fix_count}]")
            t0 = time.time()
            progress.start("Smoke checks", "re-checking after governance fix")
            _new_smoke = run_smoke_checks(run_id, mvp_dir)
            _new_arch  = verify_architecture(run_id, mvp_dir, spec, constraints)
            gov_smoke_log = _new_smoke + "\n\n" + _new_arch
            save_artifact(run_id, f"governance_smoke_log{suffix}.txt", gov_smoke_log)
            record_step_time(run_id, f"gov_smoke_{gov_fix_count}", t0)
            progress.done("Smoke checks after governance fix complete")

            # Re-run all three reviewers + meta-judge
            _, _, _, gov_verdict, gov_meta_report = _run_governance_cycle(
                gov_fix_count + 1, gov_smoke_log,
            )

        if gov_fix_count >= MAX_GOVERNANCE_ITERATIONS and governance_requires_fix(gov_meta_report):
            print(f"\n  ⚠️  Max governance fix iterations ({MAX_GOVERNANCE_ITERATIONS}) reached — proceeding.")
            log_event(run_id, "governance_max_iterations")
        else:
            print(f"\n  Governance PASS after {gov_fix_count} fix iteration(s).")

        _update_state(run_id, {
            "governance_verdict": gov_verdict,
            "governance_fix_count": gov_fix_count,
        })
        log_event(run_id, "governance_done", gov_verdict)
    else:
        reason = "--no-deepseek" if not use_deepseek else "--skip-governance"
        print(f"\n  ⏭  Governance panel skipped ({reason})")

    # ── Step 8: Final Report ─────────────────────────────────────────────────
    print("\n▶ Step 8/8  GPT-mini — writing final report")
    t0 = time.time()
    _update_state(run_id, {"current_step": "report"})
    progress.start("GPT-mini", "writing final report")
    final_report = generate_final_report(spec, mvp_dir, fix_iteration, deepseek_report)
    save_artifact(run_id, "final_mvp_report.md", final_report)
    record_step_time(run_id, "report", t0)
    progress.done("Final report written")

    # ── Total time ────────────────────────────────────────────────────────────
    total_elapsed = int(time.time() - pipeline_start)
    total_m, total_s = divmod(total_elapsed, 60)
    total_str = f"{total_m}m {total_s:02d}s" if total_m else f"{total_s}s"
    _update_state(run_id, {"status": "done", "current_step": "done", "pipeline_elapsed_s": total_elapsed})
    log_event(run_id, "done", f"total={total_str}")

    # ── Done ─────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Pipeline complete!")
    print(f"  Run folder : {rdir}")
    print(f"  Total time  : {total_str}")
    print(f"  Artifacts  :")
    for f in sorted(rdir.iterdir()):
        if f.is_file():
            print(f"        {f.name}")
    print(f"\n  MVP lives at: {mvp_dir}")
    print(f"        (see final_mvp_report.md for run commands)")
    print(f"{'='*60}\n")

    # ── Auto-launch MVP in browser ────────────────────────────────────────────
    _auto_launch_mvp(mvp_dir)

    return run_id


# ── Input helpers ─────────────────────────────────────────────────────────────

def _read_stdin() -> str:
    print("\n" + "="*60)
    print("  MVP Pipeline — paste your MVP idea below")
    print("  Type END on its own line when done.")
    print("="*60 + "\n")
    lines = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automated MVP builder pipeline.")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--input", default=None, help="Path to .md/.txt file with MVP idea")
    input_group.add_argument("--jira",  default=None, help="Jira issue key, e.g. PROJ-123")
    parser.add_argument("--resume",      default=None, help="Resume a run: --resume runs/run_001")
    parser.add_argument("--no-deepseek", action="store_true", help="Skip DeepSeek attack review")
    parser.add_argument("--run-id",      default=None, help="Use a pre-allocated run ID (from backend)")
    parser.add_argument("--mode", default="auto", choices=["auto", "idea", "requirements"],
                         help="Force input mode instead of auto-detecting")
    parser.add_argument("--plan-only", action="store_true",
                         help="Stop after generating planning artifacts "
                              "(mvp_scope.md, clean_requirements.md, mvp_spec.md, "
                              "ARCHITECTURE.md, smoke_checks.md, build_prompt.txt). "
                              "Skips Claude Code and DeepSeek entirely.")
    parser.add_argument("--skip-governance", action="store_true",
                         help="Skip the governance panel (AppSec / Legal / Infra reviews). "
                              "Governance is also skipped automatically when --no-deepseek "
                              "is set, since two of the three reviewers call DeepSeek.")
    parser.add_argument("--sprint-plan", action="store_true",
                         help="After ARCHITECTURE.md, decompose the MVP into multiple "
                              "independently-buildable sprints (sprint_plan.md / sprint_plan.json). "
                              "Only the --selected-sprint is sent to Claude Code for building.")
    parser.add_argument("--selected-sprint", type=int, default=1,
                         help="Which sprint number to build when --sprint-plan is set (default: 1).")
    parser.add_argument("--sprint-plan-only", action="store_true",
                         help="Generate the sprint plan and the selected sprint's build prompt, "
                              "then stop. Implies sprint planning; skips Claude Code and DeepSeek "
                              "entirely (like --plan-only, but sprint-aware).")

    # ── Existing App Upgrade mode ────────────────────────────────────────────
    parser.add_argument("--existing-app", default=None, metavar="PATH",
                         help="Path to an existing local app/MVP to upgrade additively. "
                              "Requires --feature-request and --upgrade-mode.")
    parser.add_argument("--feature-request", default=None, metavar="PATH",
                         help="Path to a .md/.txt file describing the feature(s) to add to "
                              "--existing-app.")
    parser.add_argument("--upgrade-mode", action="store_true",
                         help="Run Existing App Upgrade mode instead of the normal new-MVP "
                              "pipeline: inspect --existing-app, normalize --feature-request, "
                              "and plan/build additive feature sprints on top of it. Sprint 0 "
                              "is always the existing baseline and is never rebuilt.")
    parser.add_argument("--feature-sprint-plan", action="store_true",
                         help="(Existing App Upgrade mode) Decompose the requested feature work "
                              "into numbered feature sprints (feature_sprint_plan.md/.json) on "
                              "top of the Sprint 0 baseline. Currently always on in upgrade mode; "
                              "flag kept for explicit/future CLI compatibility.")
    parser.add_argument("--selected-feature-sprint", type=int, default=1, metavar="N",
                         help="(Existing App Upgrade mode) Which feature sprint number to build "
                              "(default: 1). Sprint 0 is the baseline and cannot be selected.")
    parser.add_argument("--feature-plan-only", action="store_true",
                         help="(Existing App Upgrade mode) Generate all planning artifacts "
                              "(inventory, health check, summary, requirements, gap analysis, "
                              "additive architecture, feature sprint plan, selected sprint build "
                              "prompt) then stop. Skips Claude Code and DeepSeek entirely.")

    # ── Multi-Sprint Continuation mode ───────────────────────────────────────
    parser.add_argument("--continue-run", default=None, metavar="PATH",
                         help="Continue a previous run: runs/run_NNN. The source run's preserved "
                              "sprint plan (sprint_plan.json or feature_sprint_plan.json) is loaded "
                              "as-is and its app baseline is copied into a NEW run folder — the "
                              "source run is never modified. Requires --continue-sprint.")
    parser.add_argument("--continue-sprint", type=int, default=None, metavar="N",
                         help="Which sprint number to build on top of --continue-run.")
    parser.add_argument("--continue-plan-only", action="store_true",
                         help="(Continuation mode) Generate all continuation planning artifacts "
                              "(continuation_source.md, preserved_sprint_plan.md/.json, "
                              "current_app_inventory.md, continuation_gap_analysis.md, selected "
                              "continuation sprint scope + build prompt) then stop. Skips Claude "
                              "Code and DeepSeek entirely.")
    args = parser.parse_args()

    run_id_arg = args.run_id  # may be None
    mode_arg   = None if args.mode == "auto" else args.mode
    skip_gov   = args.skip_governance  # --no-deepseek already skips via use_deepseek=False
    sprint_kwargs = dict(
        sprint_plan=args.sprint_plan or args.sprint_plan_only,
        selected_sprint=args.selected_sprint,
        sprint_plan_only=args.sprint_plan_only,
    )

    try:
        if args.continue_run:
            if args.continue_sprint is None:
                print("--continue-run requires --continue-sprint N.")
                sys.exit(1)
            pipeline_continue_sprint(
                args.continue_run,
                args.continue_sprint,
                continue_plan_only=args.continue_plan_only,
                use_deepseek=not args.no_deepseek,
                run_id=run_id_arg,
            )
        elif args.upgrade_mode:
            if not args.existing_app or not args.feature_request:
                print("--upgrade-mode requires both --existing-app PATH and --feature-request PATH.")
                sys.exit(1)
            feature_request_text = Path(args.feature_request).read_text(encoding="utf-8").strip()
            pipeline_existing_app_upgrade(
                args.existing_app,
                feature_request_text,
                run_id=run_id_arg,
                selected_feature_sprint=args.selected_feature_sprint,
                feature_plan_only=args.feature_plan_only,
                use_deepseek=not args.no_deepseek,
            )
        elif args.resume:
            resume_id = Path(args.resume).name
            raw = (run_dir(resume_id) / "raw_input.md").read_text()
            pipeline(raw, run_id=resume_id, resume=True, use_deepseek=not args.no_deepseek,
                     mode=mode_arg, plan_only=args.plan_only, skip_governance=skip_gov,
                     **sprint_kwargs)
        elif args.jira:
            from jira import format_issue_as_mvp_input
            print(f"  Fetching Jira issue {args.jira}...")
            raw = format_issue_as_mvp_input(args.jira.upper())
            print(f"\n{raw[:600]}{'...' if len(raw) > 600 else ''}\n")
            pipeline(raw, run_id=run_id_arg, use_deepseek=not args.no_deepseek, mode=mode_arg,
                     jira_used=True, plan_only=args.plan_only, skip_governance=skip_gov,
                     **sprint_kwargs)
        elif args.input:
            raw = Path(args.input).read_text(encoding="utf-8").strip()
            pipeline(raw, run_id=run_id_arg, use_deepseek=not args.no_deepseek, mode=mode_arg,
                     plan_only=args.plan_only, skip_governance=skip_gov, **sprint_kwargs)
        else:
            raw = _read_stdin()
            if not raw:
                print("No input provided. Exiting.")
                sys.exit(1)
            pipeline(raw, run_id=run_id_arg, use_deepseek=not args.no_deepseek, mode=mode_arg,
                     plan_only=args.plan_only, skip_governance=skip_gov, **sprint_kwargs)
    except RequirementsConsistencyError:
        sys.exit(1)
    except ContinuationError as e:
        print(f"\n  ❌  {e}\n")
        sys.exit(1)
