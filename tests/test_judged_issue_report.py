"""
Deterministic tests for Phase 3 judged-issue-report helpers.

Tests only the parsing / decision logic — no GPT or API calls.

Run with:
    ./venv/bin/python3 tests/test_judged_issue_report.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_mvp_builder import _parse_verdict, judged_report_requires_fix


# ── Sample reports ─────────────────────────────────────────────────────────────

PASS_REPORT = """# Judged Issue Report

## Verdict
PASS

## Summary
The build is solid. All issues raised by DeepSeek are minor styling points or noise that
do not affect whether the app meets its requirements.

## Issues

### Issue 1
- DeepSeek claim: Button colours are not brand-consistent.
- Classification: MINOR
- Reason: Cosmetic; colour choices are not specified in the requirements.
- Should fix now: no
- Fix instruction: none

### Issue 2
- DeepSeek claim: The app should have a backend for mood persistence.
- Classification: NOISE
- Reason: Requirements explicitly say frontend-only, no backend, no database.
- Should fix now: no
- Fix instruction: none

## Fix Scope
None — build meets requirements.
"""

FIX_REQUIRED_REPORT = """# Judged Issue Report

## Verdict
FIX_REQUIRED

## Summary
The build has a critical issue: clicking a mood card does not update the displayed
selection, which is the single core requirement of this app.

## Issues

### Issue 1
- DeepSeek claim: Clicking a mood card does nothing — selectedMood state is never updated.
- Classification: CRITICAL
- Reason: This is the core requirement from the spec. Without it the app does nothing useful.
- Should fix now: yes
- Fix instruction: In App.tsx, wire the onClick handler on each MoodCard to call
  setSelectedMood(mood) so the displayed selection updates.

### Issue 2
- DeepSeek claim: Card border on hover is missing.
- Classification: MINOR
- Reason: Cosmetic; not specified in the requirements.
- Should fix now: no
- Fix instruction: none

## Fix Scope
1. In App.tsx, wire the onClick handler on each MoodCard to call setSelectedMood(mood).
"""

MAJOR_ONLY_REPORT = """# Judged Issue Report

## Verdict
FIX_REQUIRED

## Summary
The app runs but one major required behaviour is broken.

## Issues

### Issue 1
- DeepSeek claim: The selected mood label shows "undefined" instead of the mood name.
- Classification: MAJOR
- Reason: Required behaviour is broken; user cannot see what they selected.
- Should fix now: yes
- Fix instruction: Pass the mood.name property instead of the mood object to the label.

## Fix Scope
1. Pass the mood.name property instead of the mood object to the selected mood label.
"""

NOISE_ONLY_REPORT = """# Judged Issue Report

## Verdict
PASS

## Issues

### Issue 1
- DeepSeek claim: There should be a REST API so moods can be fetched dynamically.
- Classification: NOISE
- Reason: Spec says frontend-only, no backend, no API.
- Should fix now: no
- Fix instruction: none

### Issue 2
- DeepSeek claim: Add a loading spinner.
- Classification: MINOR
- Reason: Not specified in requirements; purely cosmetic.
- Should fix now: no
- Fix instruction: none

## Fix Scope
None — build meets requirements.
"""

MALFORMED_REPORT = "Some random text with no Verdict section at all."

EMPTY_REPORT = ""

VERDICT_BURIED_REPORT = """# Judged Issue Report

## Summary
Things are fine.

## Verdict
PASS

## Issues
"""

MIXED_CRITICAL_NOISE_REPORT = """# Judged Issue Report

## Verdict
FIX_REQUIRED

## Issues

### Issue 1
- Classification: CRITICAL
- Should fix now: yes

### Issue 2
- Classification: NOISE
- Should fix now: no

## Fix Scope
1. Fix the critical thing.
"""


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_pass_verdict_parsed():
    assert _parse_verdict(PASS_REPORT) == "PASS"
    print("PASS: PASS verdict parsed correctly from ## Verdict section")


def test_fix_required_verdict_parsed():
    assert _parse_verdict(FIX_REQUIRED_REPORT) == "FIX_REQUIRED"
    print("PASS: FIX_REQUIRED verdict parsed correctly from ## Verdict section")


def test_major_only_is_fix_required():
    assert _parse_verdict(MAJOR_ONLY_REPORT) == "FIX_REQUIRED"
    print("PASS: MAJOR-only report correctly yields FIX_REQUIRED")


def test_noise_only_is_pass():
    assert _parse_verdict(NOISE_ONLY_REPORT) == "PASS"
    print("PASS: NOISE/MINOR-only report correctly yields PASS")


def test_malformed_report_defaults_to_fix_required():
    # Fail-safe: if we can't parse a verdict, treat as needs fix
    assert _parse_verdict(MALFORMED_REPORT) == "FIX_REQUIRED"
    print("PASS: malformed report (no Verdict section) defaults to FIX_REQUIRED (fail-safe)")


def test_empty_report_defaults_to_fix_required():
    assert _parse_verdict(EMPTY_REPORT) == "FIX_REQUIRED"
    print("PASS: empty report defaults to FIX_REQUIRED (fail-safe)")


def test_verdict_not_at_top_still_found():
    # Verdict section appears after Summary — should still be found
    assert _parse_verdict(VERDICT_BURIED_REPORT) == "PASS"
    print("PASS: verdict found even when ## Verdict is not the first section")


def test_mixed_critical_and_noise_is_fix_required():
    assert _parse_verdict(MIXED_CRITICAL_NOISE_REPORT) == "FIX_REQUIRED"
    print("PASS: mixed CRITICAL+NOISE report correctly yields FIX_REQUIRED")


def test_judged_report_requires_fix_pass_report():
    assert judged_report_requires_fix(PASS_REPORT) is False
    print("PASS: judged_report_requires_fix returns False for PASS report")


def test_judged_report_requires_fix_fix_required_report():
    assert judged_report_requires_fix(FIX_REQUIRED_REPORT) is True
    print("PASS: judged_report_requires_fix returns True for FIX_REQUIRED report")


def test_judged_report_requires_fix_major_only():
    assert judged_report_requires_fix(MAJOR_ONLY_REPORT) is True
    print("PASS: judged_report_requires_fix returns True for MAJOR-only report")


def test_judged_report_requires_fix_noise_only():
    assert judged_report_requires_fix(NOISE_ONLY_REPORT) is False
    print("PASS: judged_report_requires_fix returns False for NOISE/MINOR-only report")


def test_judged_report_requires_fix_malformed():
    # Fail-safe: unknown state → treat as needing a fix
    assert judged_report_requires_fix(MALFORMED_REPORT) is True
    print("PASS: judged_report_requires_fix returns True for unparseable report (fail-safe)")


if __name__ == "__main__":
    test_pass_verdict_parsed()
    test_fix_required_verdict_parsed()
    test_major_only_is_fix_required()
    test_noise_only_is_pass()
    test_malformed_report_defaults_to_fix_required()
    test_empty_report_defaults_to_fix_required()
    test_verdict_not_at_top_still_found()
    test_mixed_critical_and_noise_is_fix_required()
    test_judged_report_requires_fix_pass_report()
    test_judged_report_requires_fix_fix_required_report()
    test_judged_report_requires_fix_major_only()
    test_judged_report_requires_fix_noise_only()
    test_judged_report_requires_fix_malformed()
    print("\nALL TESTS PASSED")
