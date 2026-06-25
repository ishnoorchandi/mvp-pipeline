"""Smoke checks must never silently mutate a real existing app's tracked files (e.g.
`npm install` rewriting package-lock.json) and have that mutation misattributed to the
Claude build. Proves:

1. The Node install smoke check prefers `npm ci` over `npm install` when a
   package-lock.json is present (the actual root cause of the original bug report).
2. write_smoke_mutation_report() detects a tracked file changed by smoke checks.
3. A smoke-induced mutation outside the selected feature boundary blocks Local Delivery.
4. The boundary violation report distinguishes a smoke-caused change from a build-caused
   change instead of blaming the build.
5. regression_check.md reports smoke mutation status.

Uses temporary fixture apps and a fake `npm` on PATH only — never touches OneHR repos.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p

SMOKE_SCRIPT = Path(__file__).resolve().parents[1] / "smoke_checks" / "check_install_node.sh"


def fixture_app(root: Path) -> Path:
    app = root / "fixture_app"
    (app / "src/components").mkdir(parents=True)
    (app / "src/utils").mkdir(parents=True)
    (app / "src/components/DemoCard.tsx").write_text("export default function DemoCard(){return null}\n")
    (app / "src/utils/config.ts").write_text("export const CONFIG = {};\n")
    return app


def demo_sprint() -> dict:
    return {
        "sprint_number": 1,
        "title": "Add Demo Card",
        "goal": "Show a demo card.",
        "features": ["DemoCard component"],
        "likely_files_created": [],
        "likely_files_modified": ["src/components/DemoCard.tsx"],
        "must_not_modify": ["src/utils/config.ts"],
        "expected_deletions": [],
        "completion_criteria": ["Demo card renders"],
        "manual_qa_checklist": [],
    }


def _make_fake_npm(bin_dir: Path, log_path: Path) -> None:
    """A fake `npm` that records its argv and never touches anything."""
    fake_npm = bin_dir / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log_path}"\n'
        "exit 0\n"
    )
    fake_npm.chmod(fake_npm.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ── 1. The Node install smoke check prefers `npm ci` when a lockfile is present ────

def test_install_command_prefers_npm_ci_when_lockfile_present():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        (app / "package.json").write_text('{"name": "fixture-app"}\n')
        (app / "package-lock.json").write_text('{"lockfileVersion": 3}\n')

        bin_dir = root / "fakebin"
        bin_dir.mkdir()
        log_path = root / "npm_calls.log"
        _make_fake_npm(bin_dir, log_path)

        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        result = subprocess.run(
            ["bash", str(SMOKE_SCRIPT), str(app)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "npm ci" in result.stdout
        calls = log_path.read_text().strip().splitlines()
        assert calls == ["ci"], f"expected only `npm ci` to run, got: {calls}"


def test_install_command_falls_back_to_npm_install_without_lockfile():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        (app / "package.json").write_text('{"name": "fixture-app"}\n')
        # No lockfile of any kind.

        bin_dir = root / "fakebin"
        bin_dir.mkdir()
        log_path = root / "npm_calls.log"
        _make_fake_npm(bin_dir, log_path)

        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        result = subprocess.run(
            ["bash", str(SMOKE_SCRIPT), str(app)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        calls = log_path.read_text().strip().splitlines()
        assert calls == ["install --prefer-offline"], f"expected the fallback install, got: {calls}"


# ── 2. Smoke mutation report is generated when a tracked file changes during smoke ──

def test_smoke_mutation_report_generated_when_smoke_changes_tracked_file():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        (app / "package-lock.json").write_text('{"lockfileVersion": 3, "v": 1}\n')
        rdir = root / "run"
        rdir.mkdir()

        # Snapshot right after the (simulated) build finished, before smoke ran.
        p.snapshot_post_build_files(app, rdir)

        # Simulate `npm install` rewriting the lockfile during smoke checks.
        (app / "package-lock.json").write_text('{"lockfileVersion": 3, "v": 2, "extra": true}\n')

        boundary = {"expected_files_create": [], "expected_files_modify": ["src/components/DemoCard.tsx"]}
        result, content = p.write_smoke_mutation_report(app, rdir, boundary, "npm install --prefer-offline\n[PASS]")

        assert result["mutation_detected"] is True
        assert any(f["file"] == "package-lock.json" for f in result["files"])
        assert (rdir / "smoke_mutation_report.md").exists()
        assert (rdir / "smoke_mutation_report.json").exists()
        on_disk = json.loads((rdir / "smoke_mutation_report.json").read_text())
        assert on_disk == result
        assert "package-lock.json" in content
        assert "npm install" in content.lower() or "npm install/ci" in content.lower()


def test_smoke_mutation_report_clean_when_smoke_does_not_mutate():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        rdir = root / "run"
        rdir.mkdir()
        p.snapshot_post_build_files(app, rdir)
        # Nothing changes — smoke checks were non-mutating.
        result, _ = p.write_smoke_mutation_report(app, rdir, None, "[PASS] npm ci succeeded")
        assert result["mutation_detected"] is False
        assert result["should_fail_regression"] is False
        assert result["status"] == "PASS"


# ── 3. A smoke-induced mutation outside the boundary blocks Local Delivery ─────────

def test_smoke_mutation_outside_boundary_blocks_local_delivery():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        (app / "package-lock.json").write_text('{"lockfileVersion": 3, "v": 1}\n')
        rdir = root / "run"
        rdir.mkdir()
        sprint = demo_sprint()

        # Claude build legitimately changes only the in-boundary file BEFORE the
        # post-build snapshot is taken...
        (app / "src/components/DemoCard.tsx").write_text("export default function DemoCard(){return <div/>}\n")
        p.snapshot_post_build_files(app, rdir)
        # ...then smoke checks rewrite the lockfile AFTER the snapshot, outside the boundary.
        (app / "package-lock.json").write_text('{"lockfileVersion": 3, "v": 2}\n')

        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        smoke_mutation_result, _ = p.write_smoke_mutation_report(app, rdir, boundary, "npm install")

        assert smoke_mutation_result["should_fail_regression"] is True
        assert "package-lock.json" in smoke_mutation_result["out_of_boundary_files"]
        # DemoCard.tsx was already changed by the build by the time the post-build
        # snapshot was taken, so it must not show up as a smoke-induced mutation.
        assert [f["file"] for f in smoke_mutation_result["files"]] == ["package-lock.json"]

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import backend.app as app_mod

        app_mod.RUNS_DIR.mkdir(exist_ok=True)
        run_id = "run_test_smoke_mutation_block"
        run_rdir = app_mod.RUNS_DIR / run_id
        run_rdir.mkdir(exist_ok=True)
        try:
            (run_rdir / "run_state.json").write_text(json.dumps({
                "run_id": run_id, "existing_app": str(app),
                "change_boundary_status": "PASS", "boundary_violation_count": 0,
                "smoke_mutation_status": smoke_mutation_result["status"],
                "smoke_mutation_file_count": len(smoke_mutation_result["files"]),
                "smoke_mutation_blocked_delivery": True,
                "local_delivery_blocked_by_boundary": True,
            }))
            client = app_mod.app.test_client()

            r = client.post(f"/api/runs/{run_id}/delivery/commit",
                             json={"branch_name": "pipeline/test", "commit_message": "msg"})
            assert r.status_code == 409
            assert b"smoke" in r.data.lower()

            r = client.get(f"/api/runs/{run_id}/delivery")
            body = r.get_json()
            assert body["smoke_mutation"]["blocked"] is True
        finally:
            import shutil
            shutil.rmtree(run_rdir, ignore_errors=True)


# ── 4. Mutation report distinguishes smoke-check mutation from Claude build mutation ─

def test_boundary_violation_report_distinguishes_smoke_from_build_mutation():
    sprint = demo_sprint()
    boundary = p.generate_selected_feature_change_boundary(
        sprint, {"sprints": [sprint]}, "", "", Path(tempfile.mkdtemp()),
    )
    boundary_result = {
        "status": "FAIL",
        "unexpected_files": ["package-lock.json", "src/utils/config.ts"],
        "deleted_files": [],
        "unauthorized_deletions": [],
        "violations": [
            {"file": "package-lock.json", "type": "unexpected_change", "severity": "high"},
            {"file": "src/utils/config.ts", "type": "unexpected_change", "severity": "high"},
        ],
    }
    smoke_mutation_result = {
        "files": [{"file": "package-lock.json", "change_type": "modified",
                   "in_selected_feature_boundary": False, "is_known_install_artifact": True,
                   "likely_cause": "npm install/ci run during the Node smoke check"}],
    }
    with tempfile.TemporaryDirectory() as td:
        rdir = Path(td)
        content = p.write_boundary_violation_report(boundary_result, boundary, rdir, smoke_mutation_result)
        assert "package-lock.json` — caused by a smoke-check command" in content
        assert "src/utils/config.ts` — changed during the Claude build" in content
        # The smoke-caused file is explicitly exonerated, not blamed.
        lockfile_line = next(l for l in content.splitlines() if "package-lock.json" in l)
        assert "NOT by the Claude build" in lockfile_line
        config_line = next(l for l in content.splitlines() if "src/utils/config.ts" in l)
        assert "smoke-check command" not in config_line


# ── 5. regression_check.md reports smoke mutation status ───────────────────────────

def test_regression_report_includes_smoke_mutation_status():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_app(root)
        rdir = root / "run"
        rdir.mkdir()
        sprint = demo_sprint()

        p.snapshot_protected_files(app, sprint["must_not_modify"], rdir)
        p.snapshot_existing_files(app, rdir)

        # In-boundary build edit only, BEFORE the post-build snapshot — clean run.
        (app / "src/components/DemoCard.tsx").write_text("export default function DemoCard(){return <div/>}\n")
        p.snapshot_post_build_files(app, rdir)
        changed_files, _ = p.write_changed_files_report(app, rdir, sprint)
        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        boundary_result = p.check_selected_feature_boundary(changed_files, boundary)

        # Nothing changes after the post-build snapshot — smoke checks are non-mutating.
        smoke_mutation_clean, _ = p.write_smoke_mutation_report(app, rdir, boundary, "[PASS] npm ci")
        status, report = p.run_regression_check(
            app, rdir, sprint, smoke_log="[PASS] npm ci", changed_files=changed_files,
            baseline_checklist="", boundary_result=boundary_result,
            smoke_mutation_result=smoke_mutation_clean,
        )
        assert "Smoke-Induced Mutations" in report
        assert "Smoke mutation status:** PASS" in report
        assert status != "FAIL"

        # Now simulate a smoke-induced mutation outside the boundary and confirm it
        # forces the regression status to FAIL even though the build itself was clean.
        smoke_mutation_dirty = {
            "status": "FAIL", "mutation_detected": True,
            "files": [{"file": "package-lock.json", "change_type": "added",
                       "in_selected_feature_boundary": False, "is_known_install_artifact": True,
                       "likely_cause": "npm install"}],
            "out_of_boundary_files": ["package-lock.json"],
            "mutation_allowed": False, "should_fail_regression": True,
            "only_known_install_artifacts": True,
        }
        status2, report2 = p.run_regression_check(
            app, rdir, sprint, smoke_log="[PASS] npm install", changed_files=changed_files,
            baseline_checklist="", boundary_result=boundary_result,
            smoke_mutation_result=smoke_mutation_dirty,
        )
        assert status2 == "FAIL"
        assert "Local Delivery blocked by smoke mutation: Yes" in report2


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
