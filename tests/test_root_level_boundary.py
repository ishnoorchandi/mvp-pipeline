"""Selected Feature Change Boundary — root-level expected files.

Reproduces the run_088 bug report: a tiny sandbox repo where the selected sprint
asked Claude to create a single root-level file (delivery_test_note.md). Claude
created exactly that file, but Local Delivery was blocked anyway because the
boundary generator stored the expected path with a leading slash
("/delivery_test_note.md") while the actual on-disk change was reported without
one ("delivery_test_note.md") — an exact-match comparison that could never pass.

Proves:
1. A selected sprint expecting a root-level file produces a boundary that allows it.
2. '/delivery_test_note.md', './delivery_test_note.md', and 'delivery_test_note.md'
   all normalize to the same repo-relative path.
3. Root-level expected file creation passes boundary validation end to end.
4. An unexpected root-level file still fails validation, with root-specific wording.
5. A root-level .env is still blocked unless explicitly expected, and even when
   expected, is reported as a sensitive file.
6. Existing src-directory boundary behavior is unaffected by the root-level fix.

Uses temporary fixture repos only — never touches OneHR repos.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline_mvp_builder as p


def root_note_sprint(expected_path: str = "/delivery_test_note.md") -> dict:
    """Mirrors run_088's real sprint plan: a single root-level file, expressed with
    a leading slash exactly as an LLM-derived plan produced it in the bug report."""
    return {
        "sprint_number": 1,
        "title": "Add Delivery Test Note File",
        "goal": "Add a root-level note file documenting the delivery test.",
        "features": ["delivery_test_note.md"],
        "likely_files_created": [expected_path],
        "likely_files_modified": [],
        "must_not_modify": [],
        "expected_deletions": [],
        "completion_criteria": ["The 'delivery_test_note.md' file exists in the repository."],
        "manual_qa_checklist": [],
    }


def fixture_repo(root: Path) -> Path:
    app = root / "fixture_repo"
    (app / "src" / "components").mkdir(parents=True)
    (app / "src" / "components" / "Existing.tsx").write_text("export default function Existing(){return null}\n")
    (app / "README.md").write_text("# fixture repo\n")
    return app


# ── 1. A selected sprint expecting a root-level file generates an allowing boundary ──

def test_root_level_expected_file_allowed_in_generated_boundary():
    sprint = root_note_sprint("/delivery_test_note.md")
    rdir = Path(tempfile.mkdtemp())
    boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)

    assert boundary["expected_files_create"] == ["delivery_test_note.md"]
    # Root-level files must NOT grant a broad allowed directory — only the exact file.
    assert boundary["allowed_directories"] == []

    md = (rdir / "selected_feature_change_boundary.md").read_text()
    assert "delivery_test_note.md" in md
    assert "repo root" in md.lower()


# ── 2. Leading '/', './', and bare paths all normalize the same way ────────────────

def test_root_level_path_variants_normalize_identically():
    assert p._normalize_repo_relative_path("/delivery_test_note.md") == "delivery_test_note.md"
    assert p._normalize_repo_relative_path("./delivery_test_note.md") == "delivery_test_note.md"
    assert p._normalize_repo_relative_path("delivery_test_note.md") == "delivery_test_note.md"
    # Defensive: never allow escaping the repo root via '..'.
    assert ".." not in p._normalize_repo_relative_path("../../etc/passwd").split("/")
    assert p._normalize_repo_relative_path("../../etc/passwd") == "etc/passwd"


def test_boundary_generation_normalizes_all_path_variants_the_same():
    rdir = Path(tempfile.mkdtemp())
    for variant in ("/delivery_test_note.md", "./delivery_test_note.md", "delivery_test_note.md"):
        sprint = root_note_sprint(variant)
        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        assert boundary["expected_files_create"] == ["delivery_test_note.md"], variant


# ── 3. Root-level expected file creation passes boundary validation end to end ─────

def test_root_level_expected_file_creation_passes_boundary_check():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_repo(root)
        rdir = root / "run"
        rdir.mkdir()
        sprint = root_note_sprint("/delivery_test_note.md")

        p.snapshot_protected_files(app, sprint["must_not_modify"], rdir)
        p.snapshot_existing_files(app, rdir)

        # Claude creates exactly the requested root-level file — the run_088 scenario.
        (app / "delivery_test_note.md").write_text(
            "This repo was updated by the MVP Pipeline delivery test.\n"
        )

        changed_files, _ = p.write_changed_files_report(app, rdir, sprint)
        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        boundary_result = p.check_selected_feature_boundary(changed_files, boundary)

        assert boundary_result["status"] == "PASS", boundary_result
        assert boundary_result["unexpected_files"] == []

        status, report = p.run_regression_check(
            app, rdir, sprint, smoke_log="", changed_files=changed_files,
            baseline_checklist="", boundary_result=boundary_result,
        )
        assert status != "FAIL"
        assert "Boundary status:** PASS" in report


# ── 4. An unexpected root-level file still fails validation ────────────────────────

def test_unexpected_root_level_file_still_fails_boundary():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_repo(root)
        rdir = root / "run"
        rdir.mkdir()
        sprint = root_note_sprint("/delivery_test_note.md")

        p.snapshot_protected_files(app, sprint["must_not_modify"], rdir)
        p.snapshot_existing_files(app, rdir)

        # Expected file created...
        (app / "delivery_test_note.md").write_text("ok\n")
        # ...but an UNRELATED root-level file also appears (e.g. a stray scratch file).
        (app / "NOTES_SCRATCH.md").write_text("unrelated scratch notes\n")

        changed_files, _ = p.write_changed_files_report(app, rdir, sprint)
        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        boundary_result = p.check_selected_feature_boundary(changed_files, boundary)

        assert boundary_result["status"] == "FAIL"
        assert "NOTES_SCRATCH.md" in boundary_result["unexpected_files"]
        assert "delivery_test_note.md" not in boundary_result["unexpected_files"]

        content = p.write_boundary_violation_report(boundary_result, boundary, rdir)
        assert "Unexpected root-level file not listed in selected feature boundary" in content
        assert "NOTES_SCRATCH.md" in content


# ── 5. Sensitive root-level files are still blocked unless explicitly expected ─────

def test_root_level_env_file_blocked_unless_explicitly_expected():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_repo(root)
        rdir = root / "run"
        rdir.mkdir()
        sprint = root_note_sprint("/delivery_test_note.md")

        p.snapshot_protected_files(app, sprint["must_not_modify"], rdir)
        p.snapshot_existing_files(app, rdir)

        (app / "delivery_test_note.md").write_text("ok\n")
        (app / ".env").write_text("SECRET=1\n")

        changed_files, changed_report = p.write_changed_files_report(app, rdir, sprint)
        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        boundary_result = p.check_selected_feature_boundary(changed_files, boundary)

        assert boundary_result["status"] == "FAIL"
        assert ".env" in boundary_result["unexpected_files"]
        assert ".env" in changed_files["sensitive_changes"]


def test_root_level_env_file_explicitly_expected_is_still_flagged_sensitive():
    """Even when a sprint explicitly expects a root .env-like file, it's reported as
    a sensitive change so a human reviews it — explicit expectation relaxes the
    BOUNDARY check, never the sensitive-file safety reporting."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        app = fixture_repo(root)
        rdir = root / "run"
        rdir.mkdir()
        sprint = root_note_sprint("/delivery_test_note.md")
        sprint["likely_files_created"].append(".env")

        p.snapshot_protected_files(app, sprint["must_not_modify"], rdir)
        p.snapshot_existing_files(app, rdir)

        (app / "delivery_test_note.md").write_text("ok\n")
        (app / ".env").write_text("DB_NAME=example\n")

        changed_files, _ = p.write_changed_files_report(app, rdir, sprint)
        boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
        boundary_result = p.check_selected_feature_boundary(changed_files, boundary)

        assert boundary_result["status"] == "PASS"
        assert ".env" in changed_files["sensitive_changes"]


# ── 6. Existing src-directory boundary behavior is unaffected ─────────────────────

def test_src_directory_boundary_behavior_unchanged():
    sprint = {
        "sprint_number": 1, "title": "Add Demo Card", "goal": "Add a demo card.",
        "features": ["DemoCard"], "likely_files_created": ["src/components/DemoCard.tsx"],
        "likely_files_modified": ["src/components/Existing.tsx"], "must_not_modify": [],
        "expected_deletions": [], "completion_criteria": ["works"], "manual_qa_checklist": [],
    }
    rdir = Path(tempfile.mkdtemp())
    boundary = p.generate_selected_feature_change_boundary(sprint, {"sprints": [sprint]}, "", "", rdir)
    assert boundary["allowed_directories"] == ["src/components"]

    changed_files = {
        "added": ["src/components/DemoCard.tsx"],
        "modified": ["src/components/Existing.tsx"],
        "deleted": [],
    }
    result = p.check_selected_feature_boundary(changed_files, boundary)
    assert result["status"] == "PASS"

    # A new file dropped directly into the allowed subdirectory (not the exact
    # expected file) is still allowed — directory-level allowance is intentional
    # for subdirectories, unlike the repo root.
    changed_files_extra = {
        "added": ["src/components/DemoCard.tsx", "src/components/DemoCardStyles.module.css"],
        "modified": ["src/components/Existing.tsx"],
        "deleted": [],
    }
    result2 = p.check_selected_feature_boundary(changed_files_extra, boundary)
    assert result2["status"] == "PASS"

    # But a file outside that directory is still rejected.
    changed_files_bad = {
        "added": ["src/utils/unexpected.ts"],
        "modified": [],
        "deleted": [],
    }
    result3 = p.check_selected_feature_boundary(changed_files_bad, boundary)
    assert result3["status"] == "FAIL"
    assert "src/utils/unexpected.ts" in result3["unexpected_files"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
