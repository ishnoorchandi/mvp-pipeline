"""Delivery safety tests — Local Delivery + Optional Sandbox Push.

Uses temporary git fixture repos only. Never touches real OneHR repos.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delivery as d


def _git(repo: Path, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def make_repo(root: Path, name: str = "fixture-repo", remote: str | None = None, push_remote: str | None = None) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial commit")
    if remote:
        _git(repo, "remote", "add", "origin", remote)
        if push_remote and push_remote != remote:
            _git(repo, "remote", "set-url", "--push", "origin", push_remote)
    return repo


def add_dirty_file(repo: Path, relpath: str, content: str = "x\n"):
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ── 1. Company repo path blocks push ─────────────────────────────────────────

def test_company_repo_path_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "Projects" / "OneHR"
        root.mkdir(parents=True)
        repo = make_repo(root, "OneHR-UI", remote="git@github.com:ishnoorchandi/safe-demo.git")
        precheck = d.assert_clean_delivery_preconditions(repo, "sandbox_push", "demo/test")
        assert precheck["repo_type"] == "company-protected"
        assert precheck["push_allowed"] is False
        assert precheck["decision"] == "BLOCKED"


# ── 2. Remote containing OneHR-Interon blocks push ───────────────────────────

def test_onehr_interon_remote_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), remote="git@github.com-onehr:OneHR-Interon/OneHR-UI.git")
        precheck = d.assert_clean_delivery_preconditions(repo, "sandbox_push", "demo/test")
        assert precheck["repo_type"] == "company-protected"
        assert precheck["push_allowed"] is False
        assert any("OneHR-Interon" in r or "company-protected" in r for r in precheck["push_blocked_reasons"])


# ── 3. Push URL DISABLED_DO_NOT_PUSH_COMPANY_REPO blocks push ───────────────

def test_disabled_push_url_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(
            Path(td),
            remote="git@github.com-onehr:OneHR-Interon/OneHR-API-Backend.git",
            push_remote=d.DISABLED_PUSH_MARKER,
        )
        precheck = d.assert_clean_delivery_preconditions(repo, "sandbox_push", "demo/test")
        assert precheck["push_allowed"] is False
        assert precheck["decision"] == "BLOCKED"


# ── 4. Branch main blocks push ───────────────────────────────────────────────

def test_main_branch_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), remote="git@github.com:ishnoorchandi/github-delivery-demo.git")
        precheck = d.assert_clean_delivery_preconditions(repo, "sandbox_push", "main")
        assert precheck["push_allowed"] is False
        assert precheck["decision"] == "BLOCKED"
        assert any("protected branch" in r for r in precheck["push_blocked_reasons"])


# ── 5. Branch not starting with pipeline/ or demo/ blocks push ──────────────

def test_branch_without_sandbox_prefix_blocks_push():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), remote="git@github.com:ishnoorchandi/github-delivery-demo.git")
        precheck = d.assert_clean_delivery_preconditions(repo, "sandbox_push", "feature/not-sandboxed")
        assert precheck["push_allowed"] is False
        assert any("pipeline/" in r for r in precheck["push_blocked_reasons"])


# ── 6. Sandbox allowlisted repo can pass sandbox push precheck ──────────────

def test_sandbox_allowlisted_repo_passes_precheck():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), remote="git@github.com:ishnoorchandi/github-delivery-demo.git")
        precheck = d.assert_clean_delivery_preconditions(repo, "sandbox_push", "demo/proof")
        assert precheck["push_allowed"] is True
        assert precheck["decision"] == "PASS_SANDBOX_PUSH"


# ── 7. Denied files like .env or node_modules block staging/push ───────────

def test_denied_files_block_push():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), remote="git@github.com:ishnoorchandi/github-delivery-demo.git")
        add_dirty_file(repo, ".env", "SECRET=1\n")
        precheck = d.assert_clean_delivery_preconditions(repo, "sandbox_push", "demo/proof")
        assert precheck["push_allowed"] is False
        assert precheck["local_commit_allowed"] is False
        assert precheck["decision"] == "BLOCKED"


def test_denied_files_excluded_from_staging():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        add_dirty_file(repo, "safe.txt", "ok\n")
        add_dirty_file(repo, "node_modules/pkg/index.js", "module.exports = {}\n")
        result = d.stage_allowed_files(repo)
        assert "safe.txt" in result["staged"]
        assert all("node_modules" not in f for f in result["staged"])
        assert any("node_modules" in f for f in result["denied_removed"])


# ── 8. Local commit mode can pass for a safe repo ───────────────────────────

def test_local_only_mode_passes_for_safe_repo():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        add_dirty_file(repo, "feature.txt", "new feature\n")
        precheck = d.assert_clean_delivery_preconditions(repo, "local_only", "pipeline/safe-feature")
        assert precheck["decision"] == "PASS_LOCAL_ONLY"
        assert precheck["local_commit_allowed"] is True
        # local_only never allows push regardless of how safe the repo is
        assert precheck["push_allowed"] is False


def test_full_local_delivery_workflow_creates_branch_and_commit():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        add_dirty_file(repo, "feature.txt", "new feature\n")
        out_dir = Path(td) / "delivery_out"
        state = d.run_local_delivery(
            repo, mode="local_only", branch_name="pipeline/safe-feature",
            commit_message="Add feature", output_dir=out_dir,
        )
        assert state["decision"] == "PASS_LOCAL_ONLY"
        assert state["commit_hash"]
        assert "feature.txt" in state["files_committed"]
        assert state["push_attempted"] is False
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo),
                                 capture_output=True, text=True).stdout.strip()
        assert branch == "pipeline/safe-feature"
        for artifact in ("delivery_safety_check.md", "github_delivery_plan.md",
                          "changed_files_report.md", "local_commit_summary.md", "delivery_state.json"):
            assert (out_dir / artifact).exists(), f"missing {artifact}"
        assert not (out_dir / "push_result.md").exists()


# ── 9. delivery_safety_check.md includes required fields ───────────────────

def test_delivery_safety_check_md_contains_required_fields():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), remote="git@github.com:ishnoorchandi/github-delivery-demo.git",
                          push_remote=d.DISABLED_PUSH_MARKER)
        precheck = d.assert_clean_delivery_preconditions(repo, "sandbox_push", "demo/proof")
        out_path = Path(td) / "delivery_safety_check.md"
        content = d.generate_delivery_safety_check(precheck, "sandbox_push", "demo/proof", out_path)
        assert str(repo.resolve()) in content
        assert "main" in content  # current branch
        assert "github.com:ishnoorchandi/github-delivery-demo.git" in content
        assert d.DISABLED_PUSH_MARKER in content
        assert "push allowed" in content.lower() or "GitHub push allowed" in content
        assert precheck["decision"] in content


# ── 10. Tracked node_modules is a target-repo hygiene issue, not a feature bug ──
# Real OneHR UI tracks node_modules in git (bad hygiene, but pipeline must handle
# it safely): committing it, then dirtying it (simulating npm install rewriting
# files under it), reproduces the reported block without touching any real repo.

def _make_repo_with_tracked_dirty_node_modules(root: Path) -> Path:
    repo = make_repo(root)
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "index.js").write_text("module.exports = {}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "oops: tracked node_modules (bad repo hygiene)")
    # Simulate npm install touching a tracked node_modules file during smoke checks.
    (repo / "node_modules" / "pkg" / "index.js").write_text("module.exports = { v: 2 };\n")
    return repo


def test_tracked_node_modules_detected_by_repo_hygiene():
    with tempfile.TemporaryDirectory() as td:
        repo = _make_repo_with_tracked_dirty_node_modules(Path(td))
        precheck = d.assert_clean_delivery_preconditions(repo, "local_only", "pipeline/test")
        hygiene = precheck["repo_hygiene"]
        assert hygiene["node_modules_tracked"] is True
        assert hygiene["node_modules_dirty_count"] >= 1
        assert hygiene["human_cleanup_recommended"] is True
        assert hygiene["recommended_commands"]
        assert hygiene["auto_cleanup_performed"] is False


def test_denied_tracked_dependency_files_block_reason():
    with tempfile.TemporaryDirectory() as td:
        repo = _make_repo_with_tracked_dirty_node_modules(Path(td))
        precheck = d.assert_clean_delivery_preconditions(repo, "local_only", "pipeline/test")
        assert precheck["decision"] == "BLOCKED"
        assert precheck["block_reason"] == "DENIED_TRACKED_DEPENDENCY_FILES"
        assert precheck["local_commit_allowed"] is False
        # Existing safety rules must not be weakened by the new reporting.
        assert precheck["push_allowed"] is False


def test_repo_hygiene_report_includes_cleanup_recommendation():
    with tempfile.TemporaryDirectory() as td:
        repo = _make_repo_with_tracked_dirty_node_modules(Path(td))
        precheck = d.assert_clean_delivery_preconditions(repo, "local_only", "pipeline/test")
        out_dir = Path(td) / "delivery_out"
        content, json_content = d.generate_repo_hygiene_report(precheck["repo_hygiene"], out_dir)
        assert (out_dir / "repo_hygiene_report.md").exists()
        assert (out_dir / "repo_hygiene_report.json").exists()
        assert "node_modules" in content
        assert "git rm -r --cached node_modules" in content
        assert "DO NOT run automatically" in content
        assert "approved by the repo owner" in content
        data = json.loads(json_content)
        assert data["node_modules_tracked"] is True


def test_run_local_delivery_blocks_for_tracked_node_modules_without_touching_repo():
    with tempfile.TemporaryDirectory() as td:
        repo = _make_repo_with_tracked_dirty_node_modules(Path(td))
        out_dir = Path(td) / "delivery_out"
        state = d.run_local_delivery(
            repo, mode="local_only", branch_name="pipeline/test",
            commit_message="msg", output_dir=out_dir,
        )
        assert state["decision"] == "BLOCKED"
        assert state["block_reason"] == "DENIED_TRACKED_DEPENDENCY_FILES"
        assert state["commit_hash"] is None
        assert state["files_committed"] == []
        for artifact in ("repo_hygiene_report.md", "repo_hygiene_report.json", "delivery_safety_check.md"):
            assert (out_dir / artifact).exists(), f"missing {artifact}"
        # The pipeline must never stage/commit/remove node_modules itself.
        status_after = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo),
                                       capture_output=True, text=True).stdout
        assert "node_modules" in status_after
        branch_after = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo),
                                       capture_output=True, text=True).stdout.strip()
        assert branch_after == "main"  # never branched, since delivery was blocked


def test_sandbox_push_actually_pushes_to_local_bare_remote():
    """End-to-end sandbox push against a local bare repo standing in for GitHub."""
    with tempfile.TemporaryDirectory() as td:
        bare = Path(td) / "github-delivery-demo.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        repo = make_repo(Path(td), remote=str(bare))
        add_dirty_file(repo, "feature.txt", "demo feature\n")
        out_dir = Path(td) / "delivery_out"
        state = d.run_local_delivery(
            repo, mode="sandbox_push", branch_name="demo/proof",
            commit_message="Demo push", output_dir=out_dir,
            sandbox_allowlist={"github-delivery-demo"},
        )
        assert state["decision"] == "PASS_SANDBOX_PUSH"
        assert state["push_attempted"] is True
        assert state["push_succeeded"] is True
        assert (out_dir / "push_result.md").exists()
