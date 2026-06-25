"""Backend delivery endpoint tests.

Uses temporary fixture run folders only. Never touches real target repos.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend.app as app_mod


def test_delivery_endpoint_loads_plan_only_state_without_run_state_repo():
    original_runs_dir = app_mod.RUNS_DIR
    with tempfile.TemporaryDirectory() as td:
        runs_dir = Path(td) / "runs"
        app_mod.RUNS_DIR = runs_dir
        run_id = "run_fixture_plan_only"
        rdir = runs_dir / run_id
        ddir = rdir / "delivery"
        ddir.mkdir(parents=True)
        try:
            (rdir / "run_state.json").write_text(json.dumps({
                "run_id": run_id,
                "status": "done",
                "current_step": "done",
            }), encoding="utf-8")
            delivery_state = {
                "repo_path": "/tmp/fixture-delivery-repo",
                "mode": "local_only",
                "branch_name": "pipeline/github-delivery-test",
                "repo_type": "personal-sandbox",
                "decision": "PASS_LOCAL_ONLY",
                "block_reason": None,
                "repo_hygiene": {"human_cleanup_recommended": False},
                "plan_only": True,
                "commit_hash": None,
                "files_committed": [],
                "push_attempted": False,
                "push_succeeded": None,
                "note": "delivery-plan-only run - no branch, commit, or push was performed",
            }
            for name in (
                "delivery_safety_check.md",
                "github_delivery_plan.md",
                "repo_hygiene_report.md",
            ):
                (ddir / name).write_text(f"# {name}\n", encoding="utf-8")
            (ddir / "delivery_state.json").write_text(json.dumps(delivery_state), encoding="utf-8")

            client = app_mod.app.test_client()
            response = client.get(f"/api/runs/{run_id}/delivery")
            body = response.get_json()

            assert response.status_code == 200
            assert body["available"] is True
            assert "no associated git repo" not in body.get("reason", "").lower()
            assert body["repo_path"] == "/tmp/fixture-delivery-repo"
            assert body["state"]["plan_only"] is True
            assert body["state"]["decision"] == "PASS_LOCAL_ONLY"
            assert body["state"]["repo_type"] == "personal-sandbox"
            assert body["state"]["branch_name"] == "pipeline/github-delivery-test"
            assert body["state"]["push_attempted"] is False
            availability = body["artifact_availability"]
            assert availability["delivery_safety_check.md"] is True
            assert availability["delivery_state.json"] is True
            assert availability["github_delivery_plan.md"] is True
            assert availability["repo_hygiene_report.md"] is True
            assert availability["repo_hygiene_report.json"] is False
        finally:
            app_mod.RUNS_DIR = original_runs_dir


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
