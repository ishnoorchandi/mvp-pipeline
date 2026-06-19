"""
Deterministic tests for Phase 4 governance panel helpers.

Tests only parsing / decision logic — no GPT or API calls.

Run with:
    ./venv/bin/python3 tests/test_governance.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_mvp_builder import (
    _parse_governance_verdict,
    governance_requires_fix,
    generate_governance_fix_prompt,
)


# ── Sample meta-judgment reports ──────────────────────────────────────────────

PASS_META = """# Governance Meta-Judgment

## Verdict
PASS

## Summary
No significant governance issues found. The build is a simple frontend-only app with
no sensitive data handling and minimal attack surface.

## Consolidated Findings

### Issue 1
- Sources: Legal
- Finding: React library is MIT licensed — no obligations.
- Classification: NOISE
- Reason: MIT is permissive; no action required.
- Should fix now: no
- Fix instruction: none

### Issue 2
- Sources: AppSec
- Finding: No input sanitisation on mood card labels.
- Classification: MINOR
- Reason: Static hardcoded labels — no user input reaches them.
- Should fix now: no
- Fix instruction: none

## Fix Scope
None — build meets governance requirements.
"""

FIX_REQUIRED_META_CRITICAL = """# Governance Meta-Judgment

## Verdict
FIX_REQUIRED

## Summary
A hardcoded API key was found in the source; this is a critical security issue.

## Consolidated Findings

### Issue 1
- Sources: AppSec, Infra
- Finding: OPENAI_API_KEY is hardcoded in frontend/src/App.tsx line 12.
- Classification: CRITICAL
- Reason: Credential exposure in client-side code; key will be visible to any user.
- Should fix now: yes
- Fix instruction: Move the key to a .env file and load it server-side or remove it
  from client code entirely if no API call is needed.

### Issue 2
- Sources: Legal
- Finding: MIT licence on all deps — no obligations.
- Classification: NOISE
- Reason: No action required.
- Should fix now: no
- Fix instruction: none

## Fix Scope
1. Move OPENAI_API_KEY from frontend/src/App.tsx to a .env file (or remove if unused).
"""

FIX_REQUIRED_META_MAJOR = """# Governance Meta-Judgment

## Verdict
FIX_REQUIRED

## Summary
A MAJOR infrastructure issue was identified: no .env.example exists, making the project
difficult to set up for other developers.

## Consolidated Findings

### Issue 1
- Sources: Infra
- Finding: No .env.example file found; the project expects environment variables but
  provides no template.
- Classification: MAJOR
- Reason: Required for reproducible setup; an engineer cloning the repo cannot run
  the app without guessing the required variables.
- Should fix now: yes
- Fix instruction: Create .env.example listing all required environment variable names
  with placeholder values.

## Fix Scope
1. Create .env.example listing all required environment variable names.
"""

MINOR_ONLY_META = """# Governance Meta-Judgment

## Verdict
PASS

## Consolidated Findings

### Issue 1
- Classification: MINOR
- Should fix now: no
- Fix instruction: none

### Issue 2
- Classification: NOISE
- Should fix now: no
- Fix instruction: none

## Fix Scope
None — build meets governance requirements.
"""

MALFORMED_META = "Some text with no Verdict section."

EMPTY_META = ""

VERDICT_AFTER_SUMMARY_META = """# Governance Meta-Judgment

## Summary
All clear.

## Verdict
PASS

## Consolidated Findings
No findings.

## Fix Scope
None — build meets governance requirements.
"""

MIXED_CRITICAL_NOISE_META = """# Governance Meta-Judgment

## Verdict
FIX_REQUIRED

## Consolidated Findings

### Issue 1
- Classification: CRITICAL
- Should fix now: yes

### Issue 2
- Classification: NOISE
- Should fix now: no

## Fix Scope
1. Fix the critical security issue.
"""

# ── Test data for generate_governance_fix_prompt ──────────────────────────────

_SPEC = "Build a frontend-only mood picker app."
_CONSISTENCY = "RESULT: CONSISTENT WITH REQUIREMENTS"


# ── Tests: _parse_governance_verdict ──────────────────────────────────────────

def test_pass_verdict_parsed():
    assert _parse_governance_verdict(PASS_META) == "PASS"
    print("PASS: PASS verdict parsed correctly from ## Verdict section")


def test_fix_required_critical_verdict_parsed():
    assert _parse_governance_verdict(FIX_REQUIRED_META_CRITICAL) == "FIX_REQUIRED"
    print("PASS: FIX_REQUIRED verdict parsed correctly (critical finding)")


def test_fix_required_major_verdict_parsed():
    assert _parse_governance_verdict(FIX_REQUIRED_META_MAJOR) == "FIX_REQUIRED"
    print("PASS: FIX_REQUIRED verdict parsed correctly (major finding)")


def test_minor_only_is_pass():
    assert _parse_governance_verdict(MINOR_ONLY_META) == "PASS"
    print("PASS: MINOR/NOISE-only meta-judgment correctly yields PASS")


def test_malformed_defaults_to_fix_required():
    assert _parse_governance_verdict(MALFORMED_META) == "FIX_REQUIRED"
    print("PASS: malformed meta-judgment defaults to FIX_REQUIRED (fail-safe)")


def test_empty_defaults_to_fix_required():
    assert _parse_governance_verdict(EMPTY_META) == "FIX_REQUIRED"
    print("PASS: empty meta-judgment defaults to FIX_REQUIRED (fail-safe)")


def test_verdict_not_first_section_still_found():
    assert _parse_governance_verdict(VERDICT_AFTER_SUMMARY_META) == "PASS"
    print("PASS: verdict found even when ## Verdict is not the first section")


def test_mixed_critical_and_noise_is_fix_required():
    assert _parse_governance_verdict(MIXED_CRITICAL_NOISE_META) == "FIX_REQUIRED"
    print("PASS: mixed CRITICAL+NOISE correctly yields FIX_REQUIRED")


# ── Tests: governance_requires_fix ────────────────────────────────────────────

def test_governance_requires_fix_pass():
    assert governance_requires_fix(PASS_META) is False
    print("PASS: governance_requires_fix returns False for PASS report")


def test_governance_requires_fix_critical():
    assert governance_requires_fix(FIX_REQUIRED_META_CRITICAL) is True
    print("PASS: governance_requires_fix returns True for CRITICAL finding")


def test_governance_requires_fix_major():
    assert governance_requires_fix(FIX_REQUIRED_META_MAJOR) is True
    print("PASS: governance_requires_fix returns True for MAJOR finding")


def test_governance_requires_fix_minor_only():
    assert governance_requires_fix(MINOR_ONLY_META) is False
    print("PASS: governance_requires_fix returns False for MINOR/NOISE-only report")


def test_governance_requires_fix_malformed():
    assert governance_requires_fix(MALFORMED_META) is True
    print("PASS: governance_requires_fix returns True for unparseable report (fail-safe)")


# ── Tests: generate_governance_fix_prompt ─────────────────────────────────────

def test_fix_prompt_contains_critical_fix_scope():
    """The fix prompt must include the Fix Scope so Claude Code knows what to fix."""
    prompt = generate_governance_fix_prompt(
        _SPEC, Path("/tmp/mock_mvp"), FIX_REQUIRED_META_CRITICAL, _CONSISTENCY, 1,
    )
    assert "Fix Scope" in prompt or "CRITICAL" in prompt, "Expected Fix Scope or CRITICAL in prompt"
    assert "MINOR" in prompt  # the "do not address MINOR" instruction must be there
    assert "NOISE" in prompt  # same for NOISE
    print("PASS: governance fix prompt contains Fix Scope reference and MINOR/NOISE exclusion")


def test_fix_prompt_excludes_minor_noise_instruction():
    """The fix prompt must explicitly tell Claude Code NOT to fix MINOR/NOISE issues."""
    prompt = generate_governance_fix_prompt(
        _SPEC, Path("/tmp/mock_mvp"), FIX_REQUIRED_META_MAJOR, _CONSISTENCY, 1,
    )
    assert "Do NOT address MINOR or NOISE" in prompt
    print("PASS: governance fix prompt explicitly excludes MINOR/NOISE from fix scope")


def test_fix_prompt_respects_consistency_report():
    """The consistency report must appear as a hard constraint in the fix prompt."""
    consistency = "RESULT: REQUIREMENTS VIOLATED — no backend, no database"
    prompt = generate_governance_fix_prompt(
        _SPEC, Path("/tmp/mock_mvp"), FIX_REQUIRED_META_CRITICAL, consistency, 2,
    )
    assert "HARD CONSTRAINT" in prompt
    assert "no backend" in prompt
    print("PASS: governance fix prompt includes consistency report as hard constraint")


def test_fix_prompt_iteration_number():
    """Iteration number should appear in the fix prompt title."""
    prompt1 = generate_governance_fix_prompt(_SPEC, Path("/tmp/mock_mvp"), PASS_META, "", 1)
    prompt2 = generate_governance_fix_prompt(_SPEC, Path("/tmp/mock_mvp"), PASS_META, "", 2)
    assert "Iteration 1" in prompt1
    assert "Iteration 2" in prompt2
    print("PASS: governance fix prompt correctly reflects iteration number")


if __name__ == "__main__":
    # Verdict parsing
    test_pass_verdict_parsed()
    test_fix_required_critical_verdict_parsed()
    test_fix_required_major_verdict_parsed()
    test_minor_only_is_pass()
    test_malformed_defaults_to_fix_required()
    test_empty_defaults_to_fix_required()
    test_verdict_not_first_section_still_found()
    test_mixed_critical_and_noise_is_fix_required()

    # governance_requires_fix
    test_governance_requires_fix_pass()
    test_governance_requires_fix_critical()
    test_governance_requires_fix_major()
    test_governance_requires_fix_minor_only()
    test_governance_requires_fix_malformed()

    # generate_governance_fix_prompt
    test_fix_prompt_contains_critical_fix_scope()
    test_fix_prompt_excludes_minor_noise_instruction()
    test_fix_prompt_respects_consistency_report()
    test_fix_prompt_iteration_number()

    print("\nALL TESTS PASSED")
