"""Sandbox workspace + controlled build workflow.

Plan against the real repo, build only in a sandbox workspace or an explicitly
prepared safe feature branch, never build in-place on a company-protected
main/master/develop/production branch, and produce patch/diff artifacts so the
user can review before applying anything to the real repo.

Covers:
1. Sandbox copy excludes .git.
2. Sandbox copy excludes node_modules.
3. Sandbox copy excludes .env*.
4. Sandbox copy preserves source files.
5. Build gate allows company-protected repo on protected branch only when
   sandbox mode is active.
6. Build gate blocks company-protected protected branch without sandbox.
7. Active build path is sandbox path when sandbox mode is active.
8. Original repo path is preserved in run state.
9. Patch artifact is generated after sandbox changes.
10. Changed files artifact is generated after sandbox changes.
11. Original repo unchanged check passes.
12. Plan-only ignores sandbox and does not build.
13. Backend build endpoint rejects company direct build without sandbox/feature
    branch.

Fixture repos only — never touches real OneHR repos. UI rendering for sandbox
state (item 14) is covered by `npm run build`'s TypeScript check plus manual
verification — there is no JS test runner configured in this repo.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery as d
import pipeline_mvp_builder as p
import backend.app as app_mod
import planning_gate as pg


def _write_planning_approval_fixtures(run_dir: Path, app_dir: Path, git_fn) -> None:
    """Write sign-off files required by the planning gate before any build."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / pg.REQUIREMENTS_SIGNOFF_FILE).write_text(json.dumps({"status": "approved"}))
    (run_dir / pg.ARCHITECTURE_SIGNOFF_FILE).write_text(json.dumps({"status": "approved"}))
    gi = app_dir / pg.GLOBAL_INSTRUCTIONS_FILE
    gi.write_text("# Global instructions\n")
    git_fn(app_dir, "add", str(gi))
    git_fn(app_dir, "commit", "-m", "add global instructions")


def _git(repo: Path, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def make_repo(root: Path, branch: str = "main") -> Path:
    repo = root / "fixture_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", branch, str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('hello')\n")
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "index.js").write_text("module.exports = {}\n")
    (repo / ".env").write_text("SECRET=do-not-copy\n")
    (repo / ".env.local").write_text("SECRET_LOCAL=do-not-copy\n")
    (repo / "package.json").write_text(json.dumps({"name": "fixture"}))
    (repo / "README.md").write_text("# Fixture\n")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial commit")
    return repo


# ── 1-4. Sandbox copy exclusions + preservation ─────────────────────────────

def test_sandbox_copy_excludes_git():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox = root / "sandbox"
        d.create_sandbox_workspace(repo, sandbox)
        # The sandbox gets its OWN fresh git history (used only to produce a
        # diff later) — the ORIGINAL repo's .git/commit history must never be
        # copied across. Proven by: the sandbox has exactly one commit, and its
        # message is the baseline snapshot commit, not the original's "initial
        # commit".
        log = subprocess.run(
            ["git", "log", "--format=%s"], cwd=str(sandbox), check=True, capture_output=True, text=True,
        ).stdout.strip().splitlines()
        assert log == ["Sandbox baseline snapshot"]
        assert "initial commit" not in log


def test_sandbox_copy_excludes_node_modules():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox = root / "sandbox"
        d.create_sandbox_workspace(repo, sandbox)
        assert not (sandbox / "node_modules").exists()


def test_sandbox_copy_excludes_env_files():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox = root / "sandbox"
        d.create_sandbox_workspace(repo, sandbox)
        assert not (sandbox / ".env").exists()
        assert not (sandbox / ".env.local").exists()


def test_sandbox_copy_preserves_source_files():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox = root / "sandbox"
        d.create_sandbox_workspace(repo, sandbox)
        assert (sandbox / "src" / "app.py").exists()
        assert (sandbox / "src" / "app.py").read_text() == "print('hello')\n"
        assert (sandbox / "package.json").exists()
        assert (sandbox / "README.md").exists()


def test_sandbox_workspace_report_and_state_written():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox = root / "sandbox"
        state = d.create_sandbox_workspace(repo, sandbox, run_id="run_001")
        out_dir = root / "run"
        d.write_sandbox_workspace_artifacts(state, out_dir)
        assert (out_dir / "sandbox_workspace_report.md").exists()
        assert (out_dir / "sandbox_workspace_state.json").exists()
        md = (out_dir / "sandbox_workspace_report.md").read_text()
        assert "Sandbox Workspace" in md
        assert str(repo) in md
        assert str(sandbox) in md
        assert "node_modules" in md
        assert ".env*" in md
        loaded = json.loads((out_dir / "sandbox_workspace_state.json").read_text())
        assert loaded["status"] == "ready"
        assert loaded["no_push_performed"] is True


# ── 5-6. Build gate sandbox/company-repo policy ─────────────────────────────

def test_build_gate_allows_company_protected_branch_only_with_sandbox():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="main",
        sandbox_requested=True, sandbox_workspace="/tmp/sandbox-x",
        original_repo_path="/tmp/original-x",
    )
    assert gate["claude_build_allowed"] is True
    assert gate["build_workspace_mode"] == "sandbox"
    assert gate["active_build_path"] == "/tmp/sandbox-x"


def test_build_gate_blocks_company_protected_branch_without_sandbox():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="main",
        sandbox_requested=False, original_repo_path="/tmp/original-x",
    )
    assert gate["claude_build_allowed"] is False
    assert gate["build_workspace_mode"] == "none"
    assert "sandbox" in gate["reason"].lower() or "feature branch" in gate["reason"].lower()


def test_build_gate_blocks_company_protected_branch_even_with_allow_company_build():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="master", allow_company_build=True,
        sandbox_requested=False, original_repo_path="/tmp/original-x",
    )
    assert gate["claude_build_allowed"] is False


def test_build_gate_allows_company_non_protected_branch_with_sandbox():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="feature/x",
        sandbox_requested=True, sandbox_workspace="/tmp/sandbox-y",
        original_repo_path="/tmp/original-y",
    )
    assert gate["claude_build_allowed"] is True
    assert gate["build_workspace_mode"] == "sandbox"


def test_build_gate_allows_direct_branch_build_with_allow_company_build_and_clean_hygiene():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="pipeline/feature-x", allow_company_build=True,
        sandbox_requested=False, original_repo_path="/tmp/original-z",
        repo_hygiene_severity="clean",
    )
    assert gate["claude_build_allowed"] is True
    assert gate["build_workspace_mode"] == "direct"
    assert gate["active_build_path"] == "/tmp/original-z"


def test_build_gate_blocks_direct_branch_build_when_hygiene_dirty():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="pipeline/feature-x", allow_company_build=True,
        sandbox_requested=False, original_repo_path="/tmp/original-z",
        repo_hygiene_severity="blocked",
    )
    assert gate["claude_build_allowed"] is False


# ── 7-8. Active build path / original repo path in run state ───────────────

def test_active_build_path_is_sandbox_when_sandbox_mode_active():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=False, current_branch="main",
        sandbox_requested=True, sandbox_workspace="/tmp/sandbox-z",
        original_repo_path="/tmp/original-q",
    )
    assert gate["active_build_path"] == "/tmp/sandbox-z"
    assert gate["original_repo_path"] == "/tmp/original-q"


def test_active_build_path_is_original_when_sandbox_not_requested():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=False, current_branch="main",
        sandbox_requested=False, original_repo_path="/tmp/original-q",
    )
    assert gate["active_build_path"] == "/tmp/original-q"
    assert gate["build_workspace_mode"] == "direct"


def test_pipeline_existing_app_upgrade_uses_sandbox_as_active_build_path(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox_root = root / "sandboxes"
        monkeypatch.setattr(p, "RUNS_DIR", root / "runs")
        p.RUNS_DIR.mkdir()
        monkeypatch.setattr(p, "gpt", lambda *_a, **_k: "# Generated\n")
        monkeypatch.setattr(p, "gpt4o", lambda *_a, **_k: json.dumps({"sprints": [{
            "sprint_number": 1, "title": "Add Saved Filters to Resource Browser",
            "goal": "Let users save and reload named filters on the resource browser page.",
            "features": ["save filter"],
            "completion_criteria": ["A named filter can be saved and reloaded."],
            "non_goals": ["No backend or schema changes."],
            "smoke_checks": ["true"],
            "likely_files_modified": ["src/app.py"],
        }]}))
        seen_paths = []

        def fake_build(_run_id, target_path, _prompt):
            seen_paths.append(Path(target_path))
            return "fake build output"

        monkeypatch.setattr(p, "build_feature_sprint", fake_build)
        monkeypatch.setattr(p, "run_smoke_checks", lambda *_a, **_k: "PASS")

        # Planning gate requires sign-off before build; pre-write fixtures so the
        # gate sees approval without a UI interaction.
        run_id_to_use = "run_001"
        _write_planning_approval_fixtures(p.RUNS_DIR / run_id_to_use, repo, _git)

        run_id = p.pipeline_existing_app_upgrade(
            str(repo), "save filters", feature_plan_only=False, use_deepseek=False,
            use_sandbox_workspace=True, sandbox_workspace_path=str(sandbox_root / "mysandbox"),
            run_id=run_id_to_use,
        )
        state = p.load_state(run_id)
        assert state["build_workspace_mode"] == "sandbox"
        assert state["active_build_path"] == str((sandbox_root / "mysandbox").resolve())
        assert state["original_repo_path"] == str(repo.resolve())
        assert seen_paths == [(sandbox_root / "mysandbox").resolve()]
        # The original repo must never be modified by a sandbox build.
        assert not (repo / "claude_build_output.txt").exists()


# ── 9-10-11. Patch/changed-files artifacts + original repo unchanged check ──

def test_sandbox_patch_artifact_generated_after_changes():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox = root / "sandbox"
        d.create_sandbox_workspace(repo, sandbox)
        (sandbox / "src" / "app.py").write_text("print('hello world')\n")
        (sandbox / "src" / "new_feature.py").write_text("def feature(): pass\n")

        out_dir = root / "run"
        summary = d.generate_sandbox_patch_artifacts(sandbox, repo, out_dir)
        assert (out_dir / "sandbox_patch.diff").exists()
        patch_text = (out_dir / "sandbox_patch.diff").read_text()
        assert "app.py" in patch_text
        assert summary["changed_file_count"] >= 2


def test_sandbox_changed_files_artifact_generated_after_changes():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox = root / "sandbox"
        d.create_sandbox_workspace(repo, sandbox)
        (sandbox / "src" / "app.py").write_text("print('changed')\n")

        out_dir = root / "run"
        d.generate_sandbox_patch_artifacts(sandbox, repo, out_dir)
        assert (out_dir / "sandbox_changed_files.md").exists()
        changed_md = (out_dir / "sandbox_changed_files.md").read_text()
        assert "app.py" in changed_md
        assert (out_dir / "sandbox_patch_summary.md").exists()
        assert (out_dir / "apply_patch_instructions.md").exists()
        instructions = (out_dir / "apply_patch_instructions.md").read_text()
        assert "git apply" in instructions
        assert str(repo) in instructions


def test_no_changes_in_sandbox_reports_zero_changed_files():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        sandbox = root / "sandbox"
        d.create_sandbox_workspace(repo, sandbox)
        out_dir = root / "run"
        summary = d.generate_sandbox_patch_artifacts(sandbox, repo, out_dir)
        assert summary["changed_file_count"] == 0
        changed_md = (out_dir / "sandbox_changed_files.md").read_text()
        assert "No files changed" in changed_md


def test_original_repo_unchanged_check_passes_when_untouched():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        before = d.capture_repo_status_snapshot(repo)
        # Simulate a sandbox build that never touches the original repo.
        after = d.capture_repo_status_snapshot(repo)
        result = d.check_original_repo_unchanged(before, after)
        assert result["original_repo_modified"] is False
        assert result["original_repo_change_check"] == "passed"


def test_original_repo_unchanged_check_fails_when_modified():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        before = d.capture_repo_status_snapshot(repo)
        (repo / "src" / "app.py").write_text("print('SHOULD NOT HAVE CHANGED')\n")
        after = d.capture_repo_status_snapshot(repo)
        result = d.check_original_repo_unchanged(before, after)
        assert result["original_repo_modified"] is True
        assert result["original_repo_change_check"] == "failed"


def test_original_repo_unchanged_check_not_applicable_for_non_git_repo():
    with tempfile.TemporaryDirectory() as td:
        non_git = Path(td) / "plain_dir"
        non_git.mkdir()
        before = d.capture_repo_status_snapshot(non_git)
        after = d.capture_repo_status_snapshot(non_git)
        result = d.check_original_repo_unchanged(before, after)
        assert result["original_repo_change_check"] == "not_applicable"


# ── 12. Plan-only ignores sandbox and does not build ────────────────────────

def test_plan_only_ignores_sandbox_and_never_builds(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = make_repo(root)
        monkeypatch.setattr(p, "RUNS_DIR", root / "runs")
        p.RUNS_DIR.mkdir()
        monkeypatch.setattr(p, "gpt", lambda *_a, **_k: "# Generated\n")
        monkeypatch.setattr(p, "gpt4o", lambda *_a, **_k: json.dumps({"sprints": [{
            "sprint_number": 1, "title": "Add Saved Filters", "goal": "Save filters",
            "features": ["save"], "completion_criteria": ["works"],
        }]}))
        called = []
        monkeypatch.setattr(p, "build_feature_sprint", lambda *_a, **_k: called.append(True) or "")

        run_id = p.pipeline_existing_app_upgrade(
            str(repo), "save filters", feature_plan_only=True, use_deepseek=False,
            use_sandbox_workspace=True, sandbox_workspace_path=str(root / "sandboxes" / "x"),
        )
        state = p.load_state(run_id)
        assert not called, "plan-only must never invoke Claude Code build, even with sandbox flags"
        assert state["plan_only"] is True
        assert state["build_workspace_mode"] == "none"
        assert not (root / "sandboxes" / "x").exists(), "sandbox must never be created for a plan-only run"


def test_resolve_build_gate_plan_only_overrides_sandbox():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=True,
        sandbox_requested=True, sandbox_workspace="/tmp/sandbox-ignored",
        original_repo_path="/tmp/original-ignored",
    )
    assert gate["plan_only"] is True
    assert gate["claude_build_allowed"] is False
    assert gate["build_workspace_mode"] == "none"


# ── 13. Backend build endpoint rejects company direct build without sandbox ─

def test_backend_company_repo_build_guard_blocks_protected_branch():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), branch="main")
        original = app_mod.delivery_mod.is_company_repo_path
        try:
            app_mod.delivery_mod.is_company_repo_path = lambda _p: True
            error = app_mod.check_company_repo_build_guard(
                str(repo), use_sandbox_workspace=False, sandbox_workspace="", allow_company_build=False,
            )
            assert error is not None
            assert "sandbox" in error.lower() or "feature branch" in error.lower()
        finally:
            app_mod.delivery_mod.is_company_repo_path = original


def test_backend_company_repo_build_guard_allows_with_sandbox():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), branch="main")
        original = app_mod.delivery_mod.is_company_repo_path
        try:
            app_mod.delivery_mod.is_company_repo_path = lambda _p: True
            error = app_mod.check_company_repo_build_guard(
                str(repo), use_sandbox_workspace=True, sandbox_workspace="", allow_company_build=False,
            )
            assert error is None
        finally:
            app_mod.delivery_mod.is_company_repo_path = original


def test_backend_create_upgrade_run_endpoint_rejects_company_direct_build():
    original_runs_dir = app_mod.RUNS_DIR
    original_is_company = app_mod.delivery_mod.is_company_repo_path
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        runs_dir.mkdir()
        app_mod.RUNS_DIR = runs_dir
        repo = make_repo(Path(td), branch="main")
        app_mod.delivery_mod.is_company_repo_path = lambda _p: True
        try:
            client = app_mod.app.test_client()
            response = client.post("/api/runs", json={
                "upgrade_mode": True,
                "existing_app": str(repo),
                "feature_request_text": "add a feature",
                "feature_plan_only": False,
            })
            assert response.status_code == 400
            body = response.get_data(as_text=True).lower()
            assert "sandbox" in body or "feature branch" in body
        finally:
            app_mod.RUNS_DIR = original_runs_dir
            app_mod.delivery_mod.is_company_repo_path = original_is_company


def test_backend_create_upgrade_run_endpoint_allows_plan_only_for_company_repo():
    """Planning is always allowed against the original repo, even company-protected
    ones on a protected branch — only the BUILD request needs sandbox/override."""
    original_runs_dir = app_mod.RUNS_DIR
    original_is_company = app_mod.delivery_mod.is_company_repo_path
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        runs_dir.mkdir()
        app_mod.RUNS_DIR = runs_dir
        repo = make_repo(Path(td), branch="main")
        app_mod.delivery_mod.is_company_repo_path = lambda _p: True
        try:
            client = app_mod.app.test_client()
            response = client.post("/api/runs", json={
                "upgrade_mode": True,
                "existing_app": str(repo),
                "feature_request_text": "add a feature",
                "feature_plan_only": True,
            })
            assert response.status_code == 201
        finally:
            app_mod.RUNS_DIR = original_runs_dir
            app_mod.delivery_mod.is_company_repo_path = original_is_company


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
