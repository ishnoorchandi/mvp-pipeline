"""Repo hygiene classification — keeps Git/repo safety output usable for real
development. Right now a dirty `node_modules` can dump thousands of paths into
reports/UI; classify_repo_hygiene() buckets dirty paths into actionable
categories (source, dependency, generated, env/secret, lockfile, config, test,
unknown) and produces a compact, capped summary instead.

Covers:
1. node_modules dirty paths classified as dependency.
2. Source paths classified as source.
3. Env/secret paths classified as env_or_secret.
4. Generated paths classified as generated.
5. Lockfiles classified as lockfile.
6. Long dirty path lists are collapsed in markdown output.
7. repo_hygiene_state.json keeps full details (example_paths/source_examples).
8. Markdown/UI formatting never renders more than 10 dirty paths inline.
9. Git sync/pull safety includes repo_hygiene_summary.
10. Source file examples remain visible even when dependency noise dominates.

Fixture paths only — never touches real OneHR repos.
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


def make_repo(root: Path) -> Path:
    repo = root / "fixture_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial commit")
    return repo


# ── 1. node_modules dirty paths classified as dependency ───────────────────

def test_node_modules_paths_classified_as_dependency():
    paths = [f"node_modules/.bin/esbuild", "node_modules/.bin/vite", "node_modules/.package-lock.json"]
    hygiene = d.classify_repo_hygiene(paths)
    assert hygiene["dependency_files_dirty"] == 3
    assert hygiene["source_files_dirty"] == 0
    assert hygiene["severity"] == "blocked"
    assert "node_modules" in hygiene["summary"]
    assert hygiene["safe_to_build"] is False


# ── 2. Source paths classified as source ────────────────────────────────────

def test_source_paths_classified_as_source():
    paths = ["src/components/dashboard.tsx", "backend/routes/orders.py"]
    hygiene = d.classify_repo_hygiene(paths)
    assert hygiene["source_files_dirty"] == 2
    assert hygiene["dependency_files_dirty"] == 0
    assert hygiene["severity"] == "blocked"
    assert "Source file changes" in hygiene["summary"]


# ── 3. Env/secret paths classified as env_or_secret ─────────────────────────

def test_env_files_classified_as_env_or_secret():
    paths = [".env", ".env.production", "config/secrets.json"]
    hygiene = d.classify_repo_hygiene(paths)
    assert hygiene["env_or_secret_files_dirty"] == 3
    assert hygiene["severity"] == "blocked"
    assert "secret" in hygiene["summary"].lower() or "environment" in hygiene["summary"].lower()


# ── 4. Generated paths classified as generated ──────────────────────────────

def test_generated_paths_classified_as_generated():
    paths = ["dist/index.js", "frontend/build/static/main.js", "coverage/lcov.info"]
    hygiene = d.classify_repo_hygiene(paths)
    assert hygiene["generated_files_dirty"] == 3
    assert hygiene["source_files_dirty"] == 0
    assert hygiene["severity"] == "warn"


# ── 5. Lockfiles classified as lockfile ─────────────────────────────────────

def test_lockfiles_classified_as_lockfile():
    paths = ["package-lock.json", "yarn.lock", "poetry.lock"]
    hygiene = d.classify_repo_hygiene(paths)
    assert hygiene["lockfiles_dirty"] == 3
    assert hygiene["severity"] == "warn"


def test_clean_repo_is_clean_and_safe():
    hygiene = d.classify_repo_hygiene([])
    assert hygiene["severity"] == "clean"
    assert hygiene["safe_to_pull"] is True
    assert hygiene["safe_to_build"] is True
    assert hygiene["safe_to_commit"] is True


# ── 10. Source examples remain visible even when dependency noise dominates ─

def test_mixed_source_and_dependency_keeps_source_examples_visible():
    paths = ["src/components/dashboard.tsx", "src/components/admin/users.tsx"]
    paths += [f"node_modules/.bin/tool_{i}" for i in range(2728)]
    hygiene = d.classify_repo_hygiene(paths)
    assert hygiene["source_files_dirty"] == 2
    assert hygiene["dependency_files_dirty"] == 2728
    # Dependency noise drives the headline (matches the spec's worked example)...
    assert hygiene["severity"] == "blocked"
    assert "node_modules" in hygiene["summary"]
    assert len(hygiene["example_paths"]) <= 3
    # ...but source changes are never hidden — they stay visible separately.
    assert hygiene["source_examples"] == ["src/components/dashboard.tsx", "src/components/admin/users.tsx"]


# ── 6 & 8. Long dirty path lists are collapsed, never >10 rendered inline ───

def test_long_denied_path_list_collapsed_in_markdown():
    with tempfile.TemporaryDirectory() as td:
        sync_state = {
            "repo_path": td, "current_branch": "main", "fetch_url": None, "push_url": None,
            "base_branch": "main", "repo_type": "unknown", "is_company_repo": False,
            "is_dirty": True, "dirty_file_count": 2728,
            "denied_paths_dirty": True,
            "denied_dirty_paths": [f"node_modules/pkg_{i}/index.js" for i in range(2728)],
            "origin_base_exists": True, "sync_status": "up_to_date",
            "commits_ahead": 0, "commits_behind": 0, "fast_forward_safe": False,
            "pull_blocked": True, "block_reasons": [d._format_denied_paths_reason(
                [f"node_modules/pkg_{i}/index.js" for i in range(2728)]
            )],
            "fetch_attempted": True, "fetch_succeeded": True,
            "build_should_proceed": "no", "recommended_command": None,
        }
        out_path = Path(td) / "git_sync_report.md"
        content = d.generate_git_sync_report(sync_state, out_path)
        assert content.count("node_modules/pkg_") <= 3, "must not dump thousands of node_modules paths"
        assert "collapsed" in content.lower()
        assert "2728" in sync_state["block_reasons"][0]


def test_format_denied_paths_reason_never_embeds_raw_list():
    paths = [f"node_modules/pkg_{i}/index.js" for i in range(500)]
    reason = d._format_denied_paths_reason(paths)
    assert reason.count("node_modules/pkg_") <= 3
    assert "500 file" in reason
    assert "more" in reason


# ── 8. _collapse_path_list_lines never renders more than 10 paths inline ───

def test_collapse_path_list_lines_caps_at_ten():
    many_paths = [f"node_modules/pkg_{i}" for i in range(50)]
    lines = d._collapse_path_list_lines(many_paths)
    assert len(lines) == 1
    assert "collapsed" in lines[0].lower()

    few_paths = ["src/a.py", "src/b.py"]
    lines_few = d._collapse_path_list_lines(few_paths)
    assert len(lines_few) == 2


# ── 7. repo_hygiene_state.json keeps full details (capped examples + pointer) ─

def test_repo_hygiene_summary_artifacts_written(tmp_path=None):
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "run"
        paths = ["node_modules/a.js", "node_modules/b.js", "src/app.tsx"]
        hygiene = d.classify_repo_hygiene(paths, full_details_artifact="git_sync_state.json")
        md, js = d.generate_repo_hygiene_summary(hygiene, out_dir)
        assert (out_dir / "repo_hygiene_summary.md").exists()
        assert (out_dir / "repo_hygiene_state.json").exists()
        loaded = json.loads((out_dir / "repo_hygiene_state.json").read_text())
        assert loaded["dependency_files_dirty"] == 2
        assert loaded["source_files_dirty"] == 1
        assert loaded["full_details_artifact"] == "git_sync_state.json"
        assert "Full path list is available in git_sync_state.json" in md
        assert "node_modules" in md


# ── 9. Git sync/pull safety includes repo_hygiene_summary ───────────────────

def test_run_git_sync_check_writes_repo_hygiene_artifacts():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        out_dir = Path(td) / "run"
        sync_state = d.run_git_sync_check(repo, base_branch="main", output_dir=out_dir)
        assert "repo_hygiene" in sync_state
        assert sync_state["repo_hygiene"]["severity"] == "clean"
        assert (out_dir / "repo_hygiene_summary.md").exists()
        assert (out_dir / "repo_hygiene_state.json").exists()


def test_existing_app_git_sync_check_records_hygiene_on_run_state():
    original_runs_dir = p.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        try:
            p.RUNS_DIR = Path(td) / "runs"
            p.RUNS_DIR.mkdir()
            repo = make_repo(Path(td))
            # Make the repo dirty with dependency noise only.
            nm = repo / "node_modules"
            nm.mkdir()
            (nm / "pkg.js").write_text("x")
            _git(repo, "add", "-f", "node_modules/pkg.js")

            run_id = "run_hygiene_fixture"
            p.init_run(run_id, "fixture")
            sync_state = p.run_existing_app_git_sync_check(run_id, repo, base_branch="main")
            assert sync_state is not None
            state = p.load_state(run_id)
            assert state["repo_hygiene_severity"] == "blocked"
            assert "dependency" in (state["repo_hygiene_summary_text"] or "").lower() \
                or "node_modules" in (state["repo_hygiene_summary_text"] or "").lower()
            assert "repo_hygiene_summary.md" in state["git_sync_artifacts"]
            assert "repo_hygiene_state.json" in state["git_sync_artifacts"]
        finally:
            p.RUNS_DIR = original_runs_dir


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
