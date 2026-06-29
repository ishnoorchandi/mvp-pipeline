"""Build gate — single source of truth for whether Claude Code build (Step 12)
may run in Existing App Upgrade mode.

Covers:
1-3. --plan-only / --sprint-plan-only / --feature-plan-only are equivalent
     aliases: all force plan_only=True and never reach Claude Code build.
4.   Step 12 hard guard blocks Claude Code when claude_build_allowed is False.
5-6. Company-protected repo on main/master blocks build.
7.   Non-company sandbox repo can build only when not plan-only.
8.   run_state includes execution_mode/build_allowed/claude_build_allowed/
     build_gate_reason.

Fixture repos only — never touches real OneHR/OneHR-Interon repos.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery as d
import pipeline_mvp_builder as p


def _git(repo: Path, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def make_repo(root: Path, branch: str = "main") -> Path:
    repo = root / "fixture_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", branch, str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "backend").mkdir()
    (repo / "backend" / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    (repo / "frontend").mkdir()
    (repo / "frontend" / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def rich_sprint_plan_json() -> str:
    return json.dumps({"sprints": [{
        "sprint_number": 1, "title": "Add Saved Filters", "goal": "Save filters",
        "features": ["save"], "completion_criteria": ["works"],
    }]})


# ── 1-3. --plan-only / --sprint-plan-only / --feature-plan-only aliases ─────
# All three must be equivalent: plan_only True, claude_build_allowed False,
# Claude Code never invoked.

def test_resolve_build_gate_plan_only_alias_reasons_all_block_build():
    for reason in (
        "plan-only mode requested",
        "sprint-plan-only mode requested",
        "feature-plan-only mode requested",
    ):
        gate = p.resolve_build_gate(
            workflow_mode="existing_app_upgrade",
            plan_only_requested=True,
            plan_only_reason=reason,
        )
        assert gate["plan_only"] is True
        assert gate["build_allowed"] is False
        assert gate["claude_build_allowed"] is False
        assert gate["company_repo_build_allowed"] is False
        assert gate["execution_mode"] == "plan_only"
        assert gate["reason"] == reason


def test_plan_only_aliases_never_invoke_claude_build(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = make_repo(root)
        monkeypatch.setattr(p, "RUNS_DIR", root / "runs")
        p.RUNS_DIR.mkdir()
        monkeypatch.setattr(p, "gpt", lambda *_a, **_k: "# Generated\n")
        monkeypatch.setattr(p, "gpt4o", lambda *_a, **_k: rich_sprint_plan_json())
        called = []
        monkeypatch.setattr(p, "build_feature_sprint", lambda *_a, **_k: called.append(True) or "")

        for reason in (
            "plan-only mode requested",
            "sprint-plan-only mode requested",
            "feature-plan-only mode requested",
        ):
            run_id = p.pipeline_existing_app_upgrade(
                str(app), "save filters", feature_plan_only=True,
                plan_only_reason=reason, use_deepseek=False,
            )
            state = p.load_state(run_id)
            assert state["plan_only"] is True
            assert state["claude_build_allowed"] is False
            assert state["build_gate_reason"] == reason
        assert not called, "Claude Code build must never run for any plan-only alias"


# ── 4. Step 12 hard guard blocks Claude Code when claude_build_allowed is False ──

def test_step12_guard_blocks_company_protected_branch_build(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = make_repo(root, branch="main")
        monkeypatch.setattr(p, "RUNS_DIR", root / "runs")
        p.RUNS_DIR.mkdir()
        monkeypatch.setattr(p, "gpt", lambda *_a, **_k: "# Generated\n")
        monkeypatch.setattr(p, "gpt4o", lambda *_a, **_k: rich_sprint_plan_json())
        monkeypatch.setattr(d, "is_company_repo_path", lambda _path: True)
        called = []
        monkeypatch.setattr(p, "build_feature_sprint", lambda *_a, **_k: called.append(True) or "")

        run_id = p.pipeline_existing_app_upgrade(
            str(app), "save filters", feature_plan_only=False, use_deepseek=False,
        )
        state = p.load_state(run_id)
        assert not called, "Claude Code build must never run when the gate blocks it"
        assert state["claude_build_allowed"] is False
        assert state["build_allowed"] is False
        assert state["execution_mode"] == "build_blocked"
        assert "protected branch" in state["build_gate_reason"]


# ── 5-6. Company-protected repo on main/master blocks build ────────────────

def test_resolve_build_gate_blocks_company_repo_on_main():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="main", allow_company_build=True,
    )
    assert gate["claude_build_allowed"] is False
    assert gate["execution_mode"] == "build_blocked"
    assert "protected branch" in gate["reason"]


def test_resolve_build_gate_blocks_company_repo_on_master():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="master", allow_company_build=True,
    )
    assert gate["claude_build_allowed"] is False
    assert gate["execution_mode"] == "build_blocked"
    assert "protected branch" in gate["reason"]


def test_resolve_build_gate_blocks_company_repo_without_override_even_off_main():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="feature/x", allow_company_build=False,
    )
    assert gate["claude_build_allowed"] is False
    assert gate["execution_mode"] == "build_blocked"
    assert "allow-company-build" in gate["reason"]


def test_resolve_build_gate_allows_company_repo_off_protected_branch_with_override():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=True, current_branch="pipeline/feature-x", allow_company_build=True,
    )
    assert gate["claude_build_allowed"] is True
    assert gate["execution_mode"] == "build"


# ── 7. Non-company sandbox repo can build only when not plan-only ──────────

def test_resolve_build_gate_sandbox_repo_builds_when_not_plan_only():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=False,
        is_company_repo=False, current_branch="main",
    )
    assert gate["plan_only"] is False
    assert gate["build_allowed"] is True
    assert gate["claude_build_allowed"] is True
    assert gate["execution_mode"] == "build"


def test_resolve_build_gate_sandbox_repo_plan_only_blocks_build():
    gate = p.resolve_build_gate(
        workflow_mode="existing_app_upgrade", plan_only_requested=True,
        is_company_repo=False, current_branch="main",
    )
    assert gate["build_allowed"] is False
    assert gate["claude_build_allowed"] is False
    assert gate["execution_mode"] == "plan_only"


def test_sandbox_repo_build_mode_reaches_claude(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = make_repo(root, branch="pipeline/feature-x")
        monkeypatch.setattr(p, "RUNS_DIR", root / "runs")
        p.RUNS_DIR.mkdir()
        monkeypatch.setattr(p, "gpt", lambda *_a, **_k: "# Generated\n")
        monkeypatch.setattr(p, "gpt4o", lambda *_a, **_k: rich_sprint_plan_json())
        monkeypatch.setattr(p, "run_smoke_checks", lambda *_a, **_k: "PASS: npm run build")
        called = []
        monkeypatch.setattr(p, "build_feature_sprint", lambda *_a, **_k: called.append(True) or "fake build output")

        run_id = p.pipeline_existing_app_upgrade(
            str(app), "save filters", feature_plan_only=False, use_deepseek=False,
        )
        state = p.load_state(run_id)
        assert called, "a non-company, non-plan-only build must reach Claude Code"
        assert state["execution_mode"] == "build"
        assert state["claude_build_allowed"] is True


# ── 8. run_state includes the required build-gate fields ───────────────────

def test_run_state_includes_build_gate_fields_for_plan_only(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = make_repo(root)
        monkeypatch.setattr(p, "RUNS_DIR", root / "runs")
        p.RUNS_DIR.mkdir()
        monkeypatch.setattr(p, "gpt", lambda *_a, **_k: "# Generated\n")
        monkeypatch.setattr(p, "gpt4o", lambda *_a, **_k: rich_sprint_plan_json())

        run_id = p.pipeline_existing_app_upgrade(
            str(app), "save filters", feature_plan_only=True, use_deepseek=False,
        )
        state = p.load_state(run_id)
        for field in (
            "execution_mode", "plan_only", "build_allowed",
            "claude_build_allowed", "build_gate_reason", "company_repo_build_allowed",
        ):
            assert field in state, f"run_state.json missing {field}"
        assert state["execution_mode"] == "plan_only"
        assert state["plan_only"] is True
        assert state["build_allowed"] is False
        assert state["claude_build_allowed"] is False
        assert state["company_repo_build_allowed"] is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
