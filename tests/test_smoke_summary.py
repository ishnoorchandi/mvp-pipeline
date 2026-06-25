"""Smoke check summary accounting — Existing App Upgrade / MVP builder.

run_smoke.sh previously miscounted: `((PASS++)) || ((FAIL++))` makes bash's
arithmetic command return "false" whenever a counter's PRE-increment value was 0
(postfix `++` evaluates to the old value), so the very first successful check
got double-counted into FAIL too. On top of that, individual check scripts exit 0
both when they actually pass AND when they print `[SKIP] ...` (e.g. no DB_NAME in
a frontend-only app) — the orchestrator must look at that output, not just the
exit code, or a skipped check silently gets counted as a PASS instead of a SKIP.

Proves:
1. A frontend-only fixture with no DB_NAME and passing npm checks gives PASS with
   the DB check counted as SKIP, not FAIL.
2. The skipped DB check never increments the FAIL counter.
3. A real `npm run build` failure still produces a FAIL count and FAILED result.
4. regression_check.md accepts a smoke log that is PASS + SKIP (no FAIL) as
   non-failing — a skipped, non-applicable check must not fail acceptance.

Uses temporary fixture apps and the real `smoke_checks/run_smoke.sh` only — never
touches OneHR repos. No DB_NAME / psql is configured anywhere in these fixtures.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_SMOKE = REPO_ROOT / "smoke_checks" / "run_smoke.sh"


def _frontend_fixture(root: Path, build_script: str = "echo built") -> Path:
    app = root / "fixture_app"
    (app / "frontend").mkdir(parents=True)
    (app / "frontend" / "package.json").write_text(
        f'{{"name": "fixture", "scripts": {{"build": "{build_script}"}}}}\n'
    )
    (app / "frontend" / "index.html").write_text("<html></html>\n")
    (app / "README.md").write_text("# fixture\n")
    return app


def _run_smoke(app: Path) -> str:
    result = subprocess.run(
        ["bash", str(RUN_SMOKE), str(app)],
        capture_output=True, text=True, timeout=120,
    )
    return result.stdout + result.stderr


def _summary_counts(log: str) -> dict[str, int]:
    counts = {}
    for label in ("PASS", "FAIL", "SKIP"):
        m = re.search(rf"^\s*{label}\s*:\s*(\d+)\s*$", log, re.MULTILINE)
        assert m, f"no {label} count found in smoke log:\n{log}"
        counts[label] = int(m.group(1))
    return counts


# ── 1 & 2. Frontend app with no DB_NAME and passing npm checks: PASS + DB SKIP ──

def test_frontend_app_with_no_db_gives_smoke_pass_with_db_skip():
    with tempfile.TemporaryDirectory() as td:
        app = _frontend_fixture(Path(td))
        log = _run_smoke(app)

        assert "[SKIP] No DB_NAME found in .env — skipping DB check" in log
        counts = _summary_counts(log)
        assert counts["FAIL"] == 0, f"expected no failures, got: {counts}\n{log}"
        assert counts["SKIP"] >= 1, f"expected the DB check to count as SKIP: {counts}\n{log}"
        assert "RESULT: ALL CHECKS PASSED" in log


def test_skipped_db_check_does_not_increment_fail_count():
    with tempfile.TemporaryDirectory() as td:
        app = _frontend_fixture(Path(td))
        log = _run_smoke(app)
        counts = _summary_counts(log)
        # The DB check is the only optional/SKIP-able check in this fixture (no
        # backend, no requirements.txt) — its skip must not show up as a failure.
        assert counts["FAIL"] == 0
        assert "CHECK(S) FAILED" not in log


# ── 3. A real npm build failure still gives FAIL ────────────────────────────

def test_real_npm_build_failure_still_gives_fail():
    with tempfile.TemporaryDirectory() as td:
        app = _frontend_fixture(Path(td), build_script="exit 1")
        log = _run_smoke(app)

        assert "[FAIL] npm run build failed" in log
        counts = _summary_counts(log)
        assert counts["FAIL"] >= 1, f"expected a real build failure to count as FAIL: {counts}\n{log}"
        assert re.search(r"RESULT:\s*\d+\s*CHECK\(S\)\s*FAILED", log)


# ── 4. regression_check.md treats smoke PASS + SKIP as acceptable ──────────

def test_regression_accepts_pass_smoke_with_optional_skip():
    smoke_log = (
        "--- CHECK: Required files present ---\n[PASS] Entry point files found\n\n"
        "--- CHECK: npm install ---\n[PASS] install succeeded\n\n"
        "--- CHECK: npm build ---\n[PASS] npm run build succeeded\n\n"
        "--- CHECK: Database connection ---\n[SKIP] No DB_NAME found in .env — skipping DB check\n\n"
        "========================================\n"
        "  SUMMARY\n"
        "  PASS : 3\n"
        "  FAIL : 0\n"
        "  SKIP : 1\n"
        "  RESULT: ALL CHECKS PASSED\n"
        "========================================\n"
    )
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = root / "fixture_app"
        app.mkdir()
        (app / "src.ts").write_text("export const x = 1;\n")
        rdir = root / "run"
        rdir.mkdir()
        sprint = {
            "sprint_number": 1, "title": "Demo", "goal": "Demo",
            "features": ["demo"], "likely_files_created": [], "likely_files_modified": [],
            "must_not_modify": [], "expected_deletions": [], "completion_criteria": ["works"],
            "manual_qa_checklist": [],
        }
        p.snapshot_protected_files(app, [], rdir)
        p.snapshot_existing_files(app, rdir)
        changed_files, _ = p.write_changed_files_report(app, rdir, sprint)

        status, report = p.run_regression_check(
            app, rdir, sprint, smoke_log=smoke_log, changed_files=changed_files, baseline_checklist="",
        )
        assert status != "FAIL", f"a PASS+SKIP smoke log must not fail regression:\n{report}"
        assert "[fail]" not in smoke_log.lower()  # sanity: fixture truly has no FAIL marker


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
