"""
Local Delivery + Optional Sandbox Push
=======================================
Safety layer for delivering pipeline-generated changes into a real git
repository without ever risking an accidental push to a protected company
remote (OneHR-Interon) or to a protected branch (main/master/develop/production).

Three delivery modes:
  local_only    — inspect, branch, stage, commit. Never pushes.
  sandbox_push  — local_only steps + push, but ONLY if every sandbox-push
                  precondition passes (see assert_clean_delivery_preconditions).
  blocked       — unsafe combination detected; nothing is modified.

All git calls go through run_git_command(), which uses subprocess.run with an
argv list (never a shell string), so there is no shell-injection surface.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

# ── Safety constants ─────────────────────────────────────────────────────────

COMPANY_PATH_MARKER = "/Projects/OneHR/"
COMPANY_REMOTE_MARKER = "OneHR-Interon"
DISABLED_PUSH_MARKER = "DISABLED_DO_NOT_PUSH_COMPANY_REPO"

PROTECTED_BRANCHES = {"main", "master", "develop", "production"}
SANDBOX_BRANCH_PREFIXES = ("pipeline/", "demo/")

# Explicit allowlist of sandbox/demo repos that are safe to push to. Extended
# at runtime via --allow-sandbox-remote OWNER/REPO.
DEFAULT_SANDBOX_ALLOWLIST = {"ishnoorchandi/github-delivery-demo"}

# Path fragments / suffixes that must never be staged or pushed.
_DENIED_PATH_PATTERNS = [
    r"(^|/)\.env($|\.)",
    r"(^|/)node_modules(/|$)",
    r"(^|/)runs(/|$)",
    r"(^|/)venv(/|$)",
    r"(^|/)__pycache__(/|$)",
    r"(^|/)\.pytest_cache(/|$)",
    r"\.log$",
    r"(^|/)id_rsa(\.pub)?$",
    r"\.pem$",
    r"(^|/)\.ssh(/|$)",
    r"(^|/)secrets?\.(json|ya?ml|txt)$",
    r"(^|/)credentials(\.json)?$",
]
_DENIED_RE = re.compile("|".join(_DENIED_PATH_PATTERNS))


class DeliveryError(RuntimeError):
    """Raised when a git command genuinely fails (not a safety block)."""


# ── Low-level git helpers ─────────────────────────────────────────────────────

def run_git_command(repo_path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run `git <args>` in repo_path via argv (no shell), returning the CompletedProcess."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise DeliveryError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def get_git_remote_info(repo_path, remote: str = "origin") -> dict:
    """Fetch/push URLs for `remote`. Empty strings if the remote doesn't exist."""
    fetch = run_git_command(repo_path, ["remote", "get-url", remote], check=False)
    push = run_git_command(repo_path, ["remote", "get-url", "--push", remote], check=False)
    return {
        "remote": remote,
        "fetch_url": fetch.stdout.strip() if fetch.returncode == 0 else "",
        "push_url": push.stdout.strip() if push.returncode == 0 else "",
    }


def get_git_status(repo_path) -> dict:
    """Current branch + working tree cleanliness."""
    branch_res = run_git_command(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
    status_res = run_git_command(repo_path, ["status", "--porcelain"], check=False)
    lines = [l for l in status_res.stdout.splitlines() if l.strip()]
    return {"branch": branch, "clean": len(lines) == 0, "porcelain": lines}


def fetch_origin(repo_path) -> dict:
    """`git fetch origin` only — updates remote-tracking refs (origin/*), never touches
    the working tree, the index, or any local branch. Safe to run against any repo,
    including company-protected ones."""
    result = run_git_command(repo_path, ["fetch", "origin"], check=False)
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def get_ahead_behind_counts(repo_path, base_branch: str = "main") -> dict:
    """Commits HEAD is ahead/behind origin/<base_branch>, via `git rev-list --left-right
    --count HEAD...origin/<base_branch>`. Read-only. If origin/<base_branch> does not
    exist (e.g. never fetched, or base branch name is wrong), origin_base_exists is False
    and ahead/behind are both 0 — callers must check origin_base_exists before trusting
    the counts."""
    ref = f"origin/{base_branch}"
    verify = run_git_command(repo_path, ["rev-parse", "--verify", "--quiet", ref], check=False)
    if verify.returncode != 0:
        return {"origin_base_exists": False, "ahead": 0, "behind": 0}
    result = run_git_command(repo_path, ["rev-list", "--left-right", "--count", f"HEAD...{ref}"], check=False)
    if result.returncode != 0:
        return {"origin_base_exists": True, "ahead": 0, "behind": 0}
    parts = result.stdout.strip().split()
    ahead = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    behind = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return {"origin_base_exists": True, "ahead": ahead, "behind": behind}


def classify_sync_status(ahead: int, behind: int, origin_base_exists: bool) -> str:
    """up_to_date | behind | ahead | diverged | unknown."""
    if not origin_base_exists:
        return "unknown"
    if ahead == 0 and behind == 0:
        return "up_to_date"
    if ahead == 0 and behind > 0:
        return "behind"
    if ahead > 0 and behind == 0:
        return "ahead"
    return "diverged"


# ── Classification helpers ────────────────────────────────────────────────────

def is_company_repo_path(repo_path) -> bool:
    return COMPANY_PATH_MARKER in str(Path(repo_path).resolve())


def is_company_remote(remote_url: str) -> bool:
    return bool(remote_url) and COMPANY_REMOTE_MARKER in remote_url


def is_safe_sandbox_remote(remote_url: str, allowlist=None) -> bool:
    if not remote_url:
        return False
    allowlist = allowlist or DEFAULT_SANDBOX_ALLOWLIST
    return any(owner_repo in remote_url for owner_repo in allowlist)


def scan_denied_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if _DENIED_RE.search(p)]


# Dependency/build-artifact directories that get tracked by accident in real repos
# (bad repo hygiene, not a generated-feature problem) and account for the vast
# majority of "denied paths are dirty" blocks in practice.
_DEPENDENCY_DIR_RE = re.compile(r"(^|/)(node_modules|venv|__pycache__|\.pytest_cache)(/|$)")


def classify_denied_paths(denied_paths: list[str]) -> str | None:
    """Maps a list of denied/dirty paths to a stable block-reason code the frontend
    can switch on. None means no denied paths were found."""
    if not denied_paths:
        return None
    if any(_DEPENDENCY_DIR_RE.search(p) for p in denied_paths):
        return "DENIED_TRACKED_DEPENDENCY_FILES"
    return "DENIED_SENSITIVE_OR_PROTECTED_FILES"


def detect_repo_hygiene(repo_path, denied_now: list[str]) -> dict:
    """Inspects the TARGET repo (read-only — runs no mutating git commands) for the
    most common cause of a denied-paths block: `node_modules` tracked in git. Never
    auto-fixes anything; only reports facts and a human-approval-only recommendation.
    """
    repo_path = Path(repo_path)
    node_modules_tracked = False
    if (repo_path / ".git").exists():
        ls = run_git_command(repo_path, ["ls-files", "--", "node_modules"], check=False)
        node_modules_tracked = bool(ls.stdout.strip()) if ls.returncode == 0 else False

    node_modules_dirty_count = sum(1 for p in denied_now if _DEPENDENCY_DIR_RE.search(p) and "node_modules" in p)

    gitignore_has_node_modules = False
    gitignore_path = repo_path / ".gitignore"
    if gitignore_path.exists():
        for line in gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().strip("/") == "node_modules":
                gitignore_has_node_modules = True
                break

    human_cleanup_recommended = node_modules_tracked or node_modules_dirty_count > 0
    recommended_commands = [
        'printf "\\nnode_modules/\\n" >> .gitignore',
        "git rm -r --cached node_modules",
        "git add .gitignore",
        'git commit -m "Stop tracking node_modules"',
    ] if human_cleanup_recommended else []

    return {
        "node_modules_tracked": node_modules_tracked,
        "node_modules_dirty_count": node_modules_dirty_count,
        "denied_dirty_file_count": len(denied_now),
        "gitignore_has_node_modules": gitignore_has_node_modules,
        "human_cleanup_recommended": human_cleanup_recommended,
        "recommended_commands": recommended_commands,
        "auto_cleanup_performed": False,
        "requires_human_approval": human_cleanup_recommended,
    }


def detect_repo_type(repo_path, remote_info: dict) -> str:
    """company-protected | personal-sandbox | unknown."""
    if (
        is_company_repo_path(repo_path)
        or is_company_remote(remote_info.get("fetch_url", ""))
        or is_company_remote(remote_info.get("push_url", ""))
        or remote_info.get("push_url") == DISABLED_PUSH_MARKER
    ):
        return "company-protected"
    if remote_info.get("fetch_url"):
        return "personal-sandbox"
    return "unknown"


# ── Git Sync / Pull Safety ─────────────────────────────────────────────────────
# Read-only foundation for working with collaborative existing app repos (e.g.
# OneHR/OneATS) where other developers are constantly pushing. This is NOT a blind
# `git pull` — it only ever runs `git fetch origin`, `git status --short`,
# `git rev-parse`, and `git rev-list --left-right --count`. It never runs `git
# pull`, `push`, `reset`, `stash`, or anything else that mutates the working tree,
# the index, or local branch history.

def analyze_git_sync(
    repo_path,
    base_branch: str = "main",
    allow_branch_mismatch: bool = False,
    skip_fetch: bool = False,
) -> dict:
    """
    Inspect repo_path against origin/<base_branch> and report whether a fast-forward
    pull would be safe. Never raises for "unsafe" conditions — those are reported in
    block_reasons, not exceptions.

    Pull is blocked if: repo_path is not a git repo, the working tree is dirty,
    denied paths are dirty, origin/<base_branch> cannot be found, the local branch
    is ahead of or has diverged from origin/<base_branch>, the fetch itself fails,
    or the current branch isn't base_branch and allow_branch_mismatch wasn't set.
    """
    repo_path = Path(repo_path).resolve()

    repo_exists = repo_path.exists() and (repo_path / ".git").exists()
    if not repo_exists:
        return {
            "repo_path": str(repo_path), "current_branch": None, "fetch_url": None,
            "push_url": None, "base_branch": base_branch, "repo_type": "unknown",
            "is_company_repo": False, "is_dirty": None, "dirty_file_count": 0,
            "denied_paths_dirty": False, "denied_dirty_paths": [],
            "origin_base_exists": False, "sync_status": "unknown",
            "commits_ahead": 0, "commits_behind": 0, "fast_forward_safe": False,
            "pull_blocked": True, "block_reasons": ["repo path is not a git repository"],
            "fetch_attempted": False, "fetch_succeeded": None,
            "build_should_proceed": "no", "recommended_command": None,
        }

    block_reasons: list[str] = []
    fetch_attempted = not skip_fetch
    fetch_succeeded: bool | None = None
    if fetch_attempted:
        fetch_result = fetch_origin(repo_path)
        fetch_succeeded = fetch_result["success"]
        if not fetch_succeeded:
            block_reasons.append(f"git fetch origin failed: {fetch_result['stderr'] or '(unknown error)'}")

    remote_info = get_git_remote_info(repo_path)
    status = get_git_status(repo_path)
    repo_type = detect_repo_type(repo_path, remote_info)
    is_company = repo_type == "company-protected"

    dirty_paths = [line[3:].strip() for line in status["porcelain"]]
    denied_dirty = scan_denied_paths(dirty_paths)
    is_dirty = not status["clean"]

    ab = get_ahead_behind_counts(repo_path, base_branch)
    sync_status = classify_sync_status(ab["ahead"], ab["behind"], ab["origin_base_exists"])

    if not ab["origin_base_exists"]:
        block_reasons.append(f"origin/{base_branch} was not found (fetch first, or check --base-branch)")
    if is_dirty:
        block_reasons.append(f"working tree is dirty ({len(dirty_paths)} changed file(s))")
    if denied_dirty:
        block_reasons.append(f"denied paths are dirty: {denied_dirty}")
    if sync_status == "ahead":
        block_reasons.append(
            f"local branch is {ab['ahead']} commit(s) ahead of origin/{base_branch} — "
            "pulling could conflict with unpushed local work"
        )
    if sync_status == "diverged":
        block_reasons.append(
            f"local branch has diverged from origin/{base_branch} "
            f"({ab['ahead']} ahead, {ab['behind']} behind)"
        )
    if status["branch"] and status["branch"] != base_branch and not allow_branch_mismatch:
        block_reasons.append(
            f"current branch '{status['branch']}' is not the base branch '{base_branch}'"
        )

    fast_forward_safe = (
        ab["origin_base_exists"] and not is_dirty and not denied_dirty
        and sync_status == "behind"
        and (status["branch"] == base_branch or allow_branch_mismatch)
    )
    pull_blocked = len(block_reasons) > 0

    if is_dirty or denied_dirty:
        build_should_proceed = "no"
    elif sync_status in ("behind", "diverged") or pull_blocked:
        build_should_proceed = "warn"
    else:
        build_should_proceed = "yes"

    recommended_command = (
        f"git fetch origin && git pull --ff-only origin {base_branch}"
        if fast_forward_safe and not pull_blocked else None
    )

    return {
        "repo_path": str(repo_path),
        "current_branch": status["branch"] or None,
        "fetch_url": remote_info.get("fetch_url") or None,
        "push_url": remote_info.get("push_url") or None,
        "base_branch": base_branch,
        "repo_type": repo_type,
        "is_company_repo": is_company,
        "is_dirty": is_dirty,
        "dirty_file_count": len(dirty_paths),
        "denied_paths_dirty": bool(denied_dirty),
        "denied_dirty_paths": denied_dirty,
        "origin_base_exists": ab["origin_base_exists"],
        "sync_status": sync_status,
        "commits_ahead": ab["ahead"],
        "commits_behind": ab["behind"],
        "fast_forward_safe": fast_forward_safe,
        "pull_blocked": pull_blocked,
        "block_reasons": block_reasons,
        "fetch_attempted": fetch_attempted,
        "fetch_succeeded": fetch_succeeded,
        "build_should_proceed": build_should_proceed,
        "recommended_command": recommended_command,
    }


def generate_git_sync_report(sync_state: dict, output_path) -> str:
    """Writes git_sync_report.md — a plain-English explanation of sync state, never a
    command runner. Only ever shows the fast-forward command as a suggestion the user
    can choose to run manually."""
    lines = ["# Git Sync Report", ""]
    lines.append(f"**Repo path:** `{sync_state['repo_path']}`")
    lines.append(f"**Current branch:** `{sync_state.get('current_branch') or '(unknown)'}`")
    lines.append(f"**Base branch:** `{sync_state['base_branch']}`")
    lines.append(f"**Fetch URL:** `{sync_state.get('fetch_url') or '(none)'}`")
    lines.append(f"**Push URL:** `{sync_state.get('push_url') or '(none)'}`")
    lines.append(f"**Repo type:** `{sync_state.get('repo_type', 'unknown')}`")
    lines.append("")
    lines.append(f"## Sync Status: `{sync_state['sync_status']}`")
    lines.append(f"- Commits ahead of `origin/{sync_state['base_branch']}`: {sync_state['commits_ahead']}")
    lines.append(f"- Commits behind `origin/{sync_state['base_branch']}`: {sync_state['commits_behind']}")
    lines.append(f"- `origin/{sync_state['base_branch']}` exists: {sync_state['origin_base_exists']}")
    lines.append(f"- `git fetch origin` attempted: {sync_state['fetch_attempted']} "
                 f"(succeeded: {sync_state['fetch_succeeded']})")
    lines.append("")
    lines.append("## Working Tree")
    lines.append(f"- Dirty: {sync_state['is_dirty']} ({sync_state['dirty_file_count']} changed file(s))")
    lines.append(f"- Denied paths dirty: {sync_state['denied_paths_dirty']}")
    if sync_state.get("denied_dirty_paths"):
        lines.extend(f"  - `{p}`" for p in sync_state["denied_dirty_paths"])
    lines.append("")
    if sync_state.get("is_company_repo"):
        lines.append(
            "**Company repo detected.** Fetch/status checks are allowed, but pull/update must be "
            "explicitly approved. The pipeline will not discard, reset, stash, or push changes "
            "automatically."
        )
        lines.append("")
    lines.append(f"## Safe to pull (fast-forward): {sync_state['fast_forward_safe']}")
    lines.append(f"## Pull blocked: {sync_state['pull_blocked']}")
    lines.append(f"## Build should proceed: {sync_state['build_should_proceed']}")
    if sync_state.get("block_reasons"):
        lines.append("")
        lines.append("**Block reason(s):**")
        lines.extend(f"- {r}" for r in sync_state["block_reasons"])
    lines.append("")
    lines.append("## Recommended Command")
    if sync_state.get("recommended_command"):
        lines.append("Safe to run manually if you choose — this is never run automatically:")
        lines.append("```bash")
        lines.append(sync_state["recommended_command"])
        lines.append("```")
    else:
        lines.append("No fast-forward pull is recommended right now — see block reason(s) above.")

    content = "\n".join(lines) + "\n"
    Path(output_path).write_text(content, encoding="utf-8")
    return content


def run_git_sync_check(
    repo_path,
    base_branch: str = "main",
    output_dir=None,
    allow_branch_mismatch: bool = False,
    skip_fetch: bool = False,
) -> dict:
    """Orchestrator: analyze sync state and, if output_dir is given, write
    git_sync_report.md + git_sync_state.json into it. Never runs git pull, push,
    reset, or stash — analysis and reporting only."""
    sync_state = analyze_git_sync(
        repo_path, base_branch, allow_branch_mismatch=allow_branch_mismatch, skip_fetch=skip_fetch,
    )
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        generate_git_sync_report(sync_state, output_dir / "git_sync_report.md")
        (output_dir / "git_sync_state.json").write_text(json.dumps(sync_state, indent=2), encoding="utf-8")
    return sync_state


# ── Guarded fast-forward pull ───────────────────────────────────────────────────
# The ONLY mutating git command this module ever runs. It is only ever invoked
# after analyze_git_sync has confirmed fast_forward_safe — never on its own. The
# single allowed command is `git pull --ff-only origin <base_branch>`. This never
# pushes, merges, resets, stashes, checks out, or cleans anything.

def perform_ff_only_pull(repo_path, base_branch: str = "main") -> dict:
    """Runs exactly `git pull --ff-only origin <base_branch>` — no other git pull
    form is ever used. Callers must have already verified fast_forward_safe."""
    command = f"git pull --ff-only origin {base_branch}"
    result = run_git_command(repo_path, ["pull", "--ff-only", "origin", base_branch], check=False)
    return {
        "command": command,
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def generate_git_pull_report(pull_state: dict, before: dict, after: dict | None, output_path) -> str:
    """Writes git_pull_report.md — plain English summary of the guarded fast-forward
    pull attempt, including an explicit confirmation that no push/reset/stash ran."""
    lines = ["# Git Pull Report (fast-forward only)", ""]
    lines.append(f"**Repo path:** `{pull_state['repo_path']}`")
    lines.append(f"**Branch:** `{pull_state.get('current_branch') or '(unknown)'}`")
    lines.append(f"**Base branch:** `{pull_state['base_branch']}`")
    if pull_state.get("is_company_repo"):
        lines.append(
            "**Company repo detected.** This is a pull-only local update — fast-forward only, "
            "never a push, PR, reset, stash, or discard."
        )
    lines.append("")
    lines.append("## Before Pull")
    lines.append(f"- Sync status: `{before['sync_status']}`")
    lines.append(f"- Ahead/behind origin/{before['base_branch']}: {before['commits_ahead']}/{before['commits_behind']}")
    lines.append(f"- Working tree dirty: {before['is_dirty']}")
    lines.append(f"- Safe to fast-forward: {before['fast_forward_safe']}")
    lines.append("")
    lines.append(f"## Decision: `{pull_state['decision']}`")
    if pull_state["decision"] == "NO_OP":
        lines.append(
            "Pull was **not attempted** — the repository is already up to date with "
            f"`origin/{pull_state['base_branch']}`. This is a safe no-op, not a failure."
        )
        reasons = pull_state.get("block_reasons") or []
        if reasons:
            lines.append("")
            lines.append("**Reason(s):**")
            lines.extend(f"- {r}" for r in reasons)
    elif not pull_state["pull_attempted"]:
        lines.append("Pull was **not attempted** — the preflight check did not confirm it was safe.")
        reasons = pull_state.get("block_reasons") or []
        if reasons:
            lines.append("")
            lines.append("**Block reason(s):**")
            lines.extend(f"- {r}" for r in reasons)
    else:
        lines.append(f"**Command run:** `{pull_state['pull_command']}`")
        lines.append(f"**Exit code:** {pull_state['pull_exit_code']}")
        lines.append(f"**Pull succeeded:** {pull_state['pull_succeeded']}")
        if pull_state.get("pull_stdout"):
            lines.append("")
            lines.append("```")
            lines.append(pull_state["pull_stdout"])
            if pull_state.get("pull_stderr"):
                lines.append(pull_state["pull_stderr"])
            lines.append("```")
    lines.append("")
    if after is not None:
        lines.append("## After Pull")
        lines.append(f"- Sync status: `{after['sync_status']}`")
        lines.append(f"- Ahead/behind origin/{after['base_branch']}: {after['commits_ahead']}/{after['commits_behind']}")
        lines.append(f"- Working tree dirty: {after['is_dirty']}")
        lines.append("")
    lines.append(f"**Local repo now up to date:** {pull_state['now_up_to_date']}")
    lines.append(f"**New local dirty changes created by the pull:** {pull_state['new_dirty_changes_detected']}")
    lines.append("")
    lines.append("## Safety Confirmation")
    lines.append(f"- No push performed: {pull_state['no_push_performed']}")
    lines.append(f"- No reset performed: {pull_state['no_reset_performed']}")
    lines.append(f"- No stash performed: {pull_state['no_stash_performed']}")
    lines.append("- No merge, checkout, or clean was run — only `git pull --ff-only` was ever invoked.")

    content = "\n".join(lines) + "\n"
    Path(output_path).write_text(content, encoding="utf-8")
    return content


def run_git_pull_ff_only(
    repo_path,
    base_branch: str = "main",
    output_dir=None,
    allow_branch_mismatch: bool = False,
) -> dict:
    """
    Guarded fast-forward pull orchestrator:
      1. analyze_git_sync (fetch + status) — "before" state
      2. if before is already up to date (sync_status == "up_to_date" and no other
         block reasons — i.e. clean, no denied dirty paths): decision = "NO_OP".
         Nothing is run; this is a safe no-op, not a failure.
      3. else, only if before.fast_forward_safe: run
         `git pull --ff-only origin <base_branch>`
      4. analyze_git_sync again (no re-fetch needed) — "after" state
      5. write git_sync_before_pull.json / git_sync_after_pull.json / git_pull_report.md /
         git_pull_state.json into output_dir, if given

    Never runs git pull/merge/reset/stash/checkout/clean other than the single
    `git pull --ff-only origin <base_branch>` command, and only when before-pull
    analysis confirms fast_forward_safe is True.

    decision is one of: "NO_OP" (already up to date, nothing to do — success),
    "PULLED" (fast-forward pull succeeded), "FAILED" (pull was attempted but git
    reported a non-zero exit code), or "BLOCKED" (an actual safety gate failed:
    dirty, denied dirty paths, ahead, diverged, missing origin/<base_branch>,
    fetch failure, or branch mismatch).

    Returns {"state": pull_state, "before": before, "after": after_or_None}.
    """
    repo_path = Path(repo_path).resolve()
    before = analyze_git_sync(repo_path, base_branch, allow_branch_mismatch=allow_branch_mismatch)

    already_up_to_date = before["sync_status"] == "up_to_date" and not before["block_reasons"]

    pull_attempted = False
    pull_result = None
    after = None
    block_reasons: list[str] = []

    if already_up_to_date:
        decision = "NO_OP"
        after = before
        block_reasons = [f"repo is already up to date with origin/{base_branch} — nothing to pull"]
    elif before["fast_forward_safe"] and not before["block_reasons"]:
        pull_attempted = True
        pull_result = perform_ff_only_pull(repo_path, base_branch)
        after = analyze_git_sync(repo_path, base_branch, allow_branch_mismatch=allow_branch_mismatch, skip_fetch=True)
        decision = "PULLED" if pull_result["success"] else "FAILED"
    else:
        decision = "BLOCKED"
        block_reasons = list(before["block_reasons"])

    now_up_to_date = True if decision == "NO_OP" else bool(after and after["sync_status"] == "up_to_date")
    new_dirty_changes_detected = bool(after and after["is_dirty"] and not before["is_dirty"])

    state = {
        "repo_path": str(repo_path),
        "current_branch": before["current_branch"],
        "base_branch": base_branch,
        "is_company_repo": before["is_company_repo"],
        "decision": decision,
        "pull_attempted": pull_attempted,
        "pull_command": pull_result["command"] if pull_result else f"git pull --ff-only origin {base_branch}",
        "pull_exit_code": pull_result["returncode"] if pull_result else None,
        "pull_succeeded": pull_result["success"] if pull_result else None,
        "pull_stdout": pull_result["stdout"] if pull_result else "",
        "pull_stderr": pull_result["stderr"] if pull_result else "",
        "block_reasons": block_reasons,
        "now_up_to_date": now_up_to_date,
        "new_dirty_changes_detected": new_dirty_changes_detected,
        "no_push_performed": True,
        "no_reset_performed": True,
        "no_stash_performed": True,
        "timestamp": time.time(),
    }

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "git_sync_before_pull.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
        if after is not None:
            (output_dir / "git_sync_after_pull.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
        generate_git_pull_report(state, before, after, output_dir / "git_pull_report.md")
        (output_dir / "git_pull_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    return {"state": state, "before": before, "after": after}


# ── Precondition checks ───────────────────────────────────────────────────────

def assert_clean_delivery_preconditions(
    repo_path, mode: str, branch_name: str | None = None, sandbox_allowlist=None,
) -> dict:
    """
    Run every safety check and return a structured result:
      { repo_path, repo_type, remote_info, git_status, checks: {name: {status, detail}},
        local_commit_allowed, push_allowed, push_blocked_reasons, decision }
    Never raises for "unsafe" conditions — those are reported, not exceptions.
    Only raises DeliveryError for things like git itself being unusable.
    """
    sandbox_allowlist = sandbox_allowlist or DEFAULT_SANDBOX_ALLOWLIST
    repo_path = Path(repo_path).resolve()
    checks: dict[str, dict] = {}

    repo_exists = repo_path.exists() and (repo_path / ".git").exists()
    checks["target_repo_detected"] = (
        {"status": "pass", "detail": str(repo_path)}
        if repo_exists else
        {"status": "fail", "detail": f"{repo_path} is not a git repository"}
    )
    if not repo_exists:
        return {
            "repo_path": str(repo_path), "repo_type": "unknown",
            "remote_info": {}, "git_status": {}, "checks": checks,
            "local_commit_allowed": False, "push_allowed": False,
            "push_blocked_reasons": ["repo path is not a git repository"],
            "decision": "BLOCKED",
            "block_reason": "NOT_A_GIT_REPO",
            "repo_hygiene": detect_repo_hygiene(repo_path, []),
        }

    remote_info = get_git_remote_info(repo_path)
    status = get_git_status(repo_path)
    repo_type = detect_repo_type(repo_path, remote_info)
    company = repo_type == "company-protected"

    checks["company_repo_protection"] = (
        {"status": "warn", "detail": "Company repo detected — push is always blocked for this repo."}
        if company else
        {"status": "pass", "detail": "Not a recognized company-protected repo."}
    )

    # The branch we are about to create/commit on/push is the one that matters —
    # not necessarily the branch HEAD happens to be on right now (we always branch
    # off of HEAD before committing).
    target_branch = branch_name or status["branch"]
    branch_ok = target_branch not in PROTECTED_BRANCHES
    checks["current_branch_not_main"] = (
        {"status": "pass", "detail": f"Delivery branch '{target_branch}' is not protected."}
        if branch_ok else
        {"status": "fail", "detail": f"Delivery branch '{target_branch}' is a protected branch name."}
    )

    checks["working_tree_clean_before_delivery"] = (
        {"status": "pass", "detail": "Working tree was clean before delivery."}
        if status["clean"] else
        {"status": "warn", "detail": f"{len(status['porcelain'])} pending change(s) will be staged for the delivery commit."}
    )

    denied_now = scan_denied_paths([line[3:].strip() for line in status["porcelain"]])
    checks["denied_files_not_staged"] = (
        {"status": "fail", "detail": f"Denied paths present in working tree: {denied_now}"}
        if denied_now else
        {"status": "pass", "detail": "No denied paths (.env, node_modules, runs, logs, secrets) detected."}
    )

    local_commit_allowed = branch_ok and not denied_now
    checks["local_commit_allowed"] = (
        {"status": "pass", "detail": "Local branch + commit can proceed."}
        if local_commit_allowed else
        {"status": "fail", "detail": "Blocked by protected branch name or denied files."}
    )

    push_blocked_reasons: list[str] = []
    if mode != "sandbox_push":
        push_blocked_reasons.append("delivery mode is local_only — push was not requested")
    if company:
        push_blocked_reasons.append("repo is company-protected (path under /Projects/OneHR/, OneHR-Interon remote, or disabled push URL)")
    if remote_info.get("push_url") == DISABLED_PUSH_MARKER:
        push_blocked_reasons.append(f"push URL is '{DISABLED_PUSH_MARKER}'")
    if not branch_ok:
        push_blocked_reasons.append(f"delivery branch '{target_branch}' is a protected branch name")
    if not target_branch.startswith(SANDBOX_BRANCH_PREFIXES):
        push_blocked_reasons.append(f"delivery branch '{target_branch}' does not start with 'pipeline/' or 'demo/'")
    candidate_remote = remote_info.get("fetch_url") or remote_info.get("push_url") or ""
    if not is_safe_sandbox_remote(candidate_remote, sandbox_allowlist):
        push_blocked_reasons.append("remote is not in the sandbox allowlist")
    if denied_now:
        push_blocked_reasons.append("denied paths are present and would be staged")

    push_allowed = len(push_blocked_reasons) == 0
    checks["github_push_allowed"] = (
        {"status": "pass", "detail": "All sandbox push conditions are satisfied."}
        if push_allowed else
        {"status": "fail" if mode == "sandbox_push" else "warn", "detail": "; ".join(push_blocked_reasons)}
    )

    if not local_commit_allowed:
        decision = "BLOCKED"
    elif mode == "sandbox_push":
        decision = "PASS_SANDBOX_PUSH" if push_allowed else "BLOCKED"
    else:
        decision = "PASS_LOCAL_ONLY"

    # block_reason is None unless local commit itself is blocked — a sandbox-push-only
    # block (e.g. non-allowlisted remote) is not a "blocked delivery", local_only still
    # proceeds. denied_now takes priority over the protected-branch reason since it's
    # almost always a target-repo hygiene issue (e.g. tracked node_modules), not a
    # generated-feature problem, and that distinction is what the UI needs to show.
    block_reason = None
    if not local_commit_allowed:
        block_reason = classify_denied_paths(denied_now) if denied_now else "PROTECTED_BRANCH"

    return {
        "repo_path": str(repo_path),
        "repo_type": repo_type,
        "remote_info": remote_info,
        "git_status": status,
        "checks": checks,
        "local_commit_allowed": local_commit_allowed,
        "push_allowed": push_allowed,
        "push_blocked_reasons": push_blocked_reasons,
        "decision": decision,
        "block_reason": block_reason,
        "repo_hygiene": detect_repo_hygiene(repo_path, denied_now),
    }


# ── Branch / stage / commit / push operations ────────────────────────────────

def create_local_delivery_branch(repo_path, branch_name: str) -> str:
    """Create+checkout branch_name off current HEAD, or checkout it if it already exists."""
    existing = run_git_command(repo_path, ["rev-parse", "--verify", "--quiet", branch_name], check=False)
    if existing.returncode == 0:
        run_git_command(repo_path, ["checkout", branch_name])
    else:
        run_git_command(repo_path, ["checkout", "-b", branch_name])
    return branch_name


def stage_allowed_files(repo_path, allowlist: list[str] | None = None, denylist: list[str] | None = None) -> dict:
    """Stage files (allowlist of paths, or everything via `git add -A`), then unstage
    anything matching the denylist (or the built-in denied-path patterns)."""
    if allowlist:
        run_git_command(repo_path, ["add", "--", *allowlist])
    else:
        run_git_command(repo_path, ["add", "-A"])

    staged = [s for s in run_git_command(repo_path, ["diff", "--cached", "--name-only"]).stdout.splitlines() if s.strip()]

    if denylist:
        denied_matches = [s for s in staged if any(d in s for d in denylist)]
    else:
        denied_matches = scan_denied_paths(staged)

    if denied_matches:
        run_git_command(repo_path, ["reset", "HEAD", "--", *denied_matches], check=False)
        staged = [s for s in staged if s not in denied_matches]

    return {"staged": staged, "denied_removed": denied_matches}


def create_local_delivery_commit(repo_path, message: str) -> dict | None:
    """Commit currently staged files. Returns None if nothing is staged."""
    staged = [s for s in run_git_command(repo_path, ["diff", "--cached", "--name-only"]).stdout.splitlines() if s.strip()]
    if not staged:
        return None
    run_git_command(repo_path, ["commit", "-m", message])
    commit_hash = run_git_command(repo_path, ["rev-parse", "HEAD"]).stdout.strip()
    author = run_git_command(repo_path, ["log", "-1", "--pretty=%an <%ae>"]).stdout.strip()
    return {"hash": commit_hash, "author": author, "message": message, "files": staged}


def push_sandbox_branch(repo_path, branch_name: str) -> dict:
    """Push branch_name to origin. Only call this after a PASS_SANDBOX_PUSH decision."""
    result = run_git_command(repo_path, ["push", "-u", "origin", branch_name], check=False)
    return {
        "success": result.returncode == 0,
        "branch": branch_name,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "returncode": result.returncode,
    }


# ── Report generation ─────────────────────────────────────────────────────────

def _github_web_url(remote_url: str, branch: str) -> str | None:
    """Best-effort https://github.com/OWNER/REPO/tree/BRANCH derivation. None if not derivable."""
    if not remote_url:
        return None
    m = re.search(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(\.git)?$", remote_url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    return f"https://github.com/{owner}/{repo}/tree/{branch}"


def generate_changed_files_report(repo_path, output_path) -> tuple[str, dict]:
    """Diff of what's currently staged (git diff --cached). Writes changed_files_report.md."""
    name_status = run_git_command(repo_path, ["diff", "--cached", "--name-status"], check=False).stdout.splitlines()
    added, modified, deleted, renamed = [], [], [], []
    for line in name_status:
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0]
        if code.startswith("A"):
            added.append(parts[1])
        elif code.startswith("M"):
            modified.append(parts[1])
        elif code.startswith("D"):
            deleted.append(parts[1])
        elif code.startswith("R"):
            renamed.append(f"{parts[1]} -> {parts[2]}")

    all_paths = added + modified + deleted + [r.split(" -> ")[1] for r in renamed]
    denied = scan_denied_paths(all_paths)

    lines = ["# Changed Files Report", ""]
    lines.append(f"**Total staged files:** {len(all_paths)}")
    lines.append("")
    for label, items in (("Added", added), ("Modified", modified), ("Deleted", deleted), ("Renamed", renamed)):
        lines.append(f"## {label} ({len(items)})")
        lines.extend(f"- `{f}`" for f in items) if items else lines.append("- (none)")
        lines.append("")
    lines.append(f"## Denied / Risky Paths Detected ({len(denied)})")
    if denied:
        lines.append("**These paths were detected and were NOT included in the delivery commit:**")
        lines.extend(f"- `{f}`" for f in denied)
    else:
        lines.append("- (none — no `.env`, `node_modules/`, `runs/`, `venv/`, logs, or secret-like files detected)")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"{len(all_paths) - len(denied)} file(s) staged and committed; {len(denied)} denied file(s) excluded.")

    content = "\n".join(lines) + "\n"
    Path(output_path).write_text(content, encoding="utf-8")
    data = {"added": added, "modified": modified, "deleted": deleted, "renamed": renamed, "denied": denied}
    return content, data


def generate_delivery_safety_check(precheck: dict, mode: str, branch_name: str | None, output_path) -> str:
    remote_info = precheck.get("remote_info", {})
    status = precheck.get("git_status", {})
    lines = ["# Delivery Safety Check", ""]
    lines.append(f"**Repo path:** `{precheck['repo_path']}`")
    lines.append(f"**Current branch:** `{status.get('branch', '(unknown)')}`")
    lines.append(f"**Delivery branch:** `{branch_name or status.get('branch', '(unknown)')}`")
    lines.append(f"**Fetch URL:** `{remote_info.get('fetch_url') or '(none)'}`")
    lines.append(f"**Push URL:** `{remote_info.get('push_url') or '(none)'}`")
    lines.append(f"**Detected repo type:** `{precheck['repo_type']}`")
    lines.append(f"**Working tree clean:** {status.get('clean', '(unknown)')}")
    lines.append(f"**Requested mode:** `{mode}`")
    lines.append("")
    lines.append("## Safety Checklist")
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    for name, result in precheck.get("checks", {}).items():
        lines.append(f"- {icon.get(result['status'], '•')} **{name.replace('_', ' ')}** — {result['detail']}")
    lines.append("")
    lines.append(f"**Local commit allowed:** {precheck['local_commit_allowed']}")
    lines.append(f"**GitHub push allowed:** {precheck['push_allowed']}")
    if precheck.get("push_blocked_reasons"):
        lines.append("")
        lines.append("**Push blocked reasons:**")
        lines.extend(f"- {r}" for r in precheck["push_blocked_reasons"])
    if precheck.get("block_reason"):
        lines.append("")
        lines.append(f"**Block reason:** `{precheck['block_reason']}`")
        if precheck["block_reason"] == "DENIED_TRACKED_DEPENDENCY_FILES":
            hygiene = precheck.get("repo_hygiene") or {}
            lines += [
                "",
                "This is a TARGET REPO HYGIENE issue, not a problem with the generated feature. "
                "Dependency files (e.g. `node_modules/`) are tracked and/or dirty in this repo, so "
                "Local Delivery is blocked — the pipeline will never stage or commit them.",
                f"- `node_modules` tracked in git: {hygiene.get('node_modules_tracked')}",
                f"- Dirty denied file count: {hygiene.get('denied_dirty_file_count')}",
                f"- `.gitignore` already excludes `node_modules/`: {hygiene.get('gitignore_has_node_modules')}",
                "- See repo_hygiene_report.md for the recommended (human-approval-only) cleanup commands.",
            ]
    lines.append("")
    lines.append(f"## Final Decision: `{precheck['decision']}`")
    decision_text = {
        "PASS_LOCAL_ONLY": "A local branch and commit will be created. Nothing will be pushed to GitHub.",
        "PASS_SANDBOX_PUSH": "A local branch/commit will be created AND pushed to the allowlisted sandbox remote only.",
        "BLOCKED": "No changes were made. See the checklist above for the reason(s).",
    }
    lines.append(decision_text.get(precheck["decision"], ""))

    content = "\n".join(lines) + "\n"
    Path(output_path).write_text(content, encoding="utf-8")
    return content


def generate_repo_hygiene_report(hygiene: dict, output_dir) -> tuple[str, str]:
    """Writes repo_hygiene_report.md/.json. Purely informational — never executes any
    of the recommended commands, and never auto-restores or auto-untracks anything in
    the target repo. Returns (markdown, json_text)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Repo Hygiene Report", ""]
    lines.append(f"**`node_modules` tracked in git:** {hygiene.get('node_modules_tracked', False)}")
    lines.append(f"**`node_modules` dirty/changed file count:** {hygiene.get('node_modules_dirty_count', 0)}")
    lines.append(f"**Total denied dirty file count:** {hygiene.get('denied_dirty_file_count', 0)}")
    lines.append(f"**`.gitignore` already excludes `node_modules/`:** {hygiene.get('gitignore_has_node_modules', False)}")
    lines.append("")
    if hygiene.get("human_cleanup_recommended"):
        lines += [
            "## What This Means",
            "Local Delivery is blocked because dependency files under `node_modules` are tracked "
            "and/or dirty in the TARGET repository. **This is a target-repo hygiene issue, not a "
            "problem with the generated feature.** The pipeline will never stage, commit, or remove "
            "`node_modules` automatically.",
            "",
            "## Recommended Fix — requires human approval, DO NOT run automatically",
            "```bash",
        ]
        lines += hygiene.get("recommended_commands") or []
        lines += [
            "```",
            "",
            "- Do not run these commands automatically — the pipeline never executes them.",
            "- Do not push to a company-protected repository.",
            "- This should be approved by the repo owner/team before running.",
            "- No branch, commit, or push was attempted by this run.",
        ]
    else:
        lines.append("No `node_modules` tracking/hygiene issue detected.")
    content = "\n".join(lines) + "\n"
    (output_dir / "repo_hygiene_report.md").write_text(content, encoding="utf-8")
    json_content = json.dumps(hygiene, indent=2)
    (output_dir / "repo_hygiene_report.json").write_text(json_content, encoding="utf-8")
    return content, json_content


def generate_github_delivery_plan(precheck: dict, mode: str, branch_name: str | None, output_path) -> str:
    remote_info = precheck.get("remote_info", {})
    status = precheck.get("git_status", {})
    target_branch = branch_name or status.get("branch", "(unknown)")
    lines = ["# GitHub Delivery Plan", ""]

    if precheck["decision"] == "PASS_SANDBOX_PUSH":
        lines.append("```")
        lines.append("Push status: ALLOWED_SANDBOX_ONLY")
        lines.append(f"Remote: {remote_info.get('fetch_url') or remote_info.get('push_url')}")
        lines.append(f"Branch: {target_branch}")
        lines.append(f"Push command: git push origin {target_branch}")
        lines.append("```")
    elif precheck["decision"] == "PASS_LOCAL_ONLY":
        lines.append("```")
        lines.append("Push status: DISABLED")
        lines.append("Reason: Company repo protected, or local-only mode requested. This workflow")
        lines.append("created a local branch/commit only.")
        lines.append("No changes were published to GitHub.")
        lines.append("```")
    else:
        lines.append("```")
        lines.append("Push status: BLOCKED")
        lines.append("Reason: One or more safety preconditions failed. No branch, commit, or push")
        lines.append("was performed. See delivery_safety_check.md for details.")
        lines.append("```")

    content = "\n".join(lines) + "\n"
    Path(output_path).write_text(content, encoding="utf-8")
    return content


def generate_local_commit_summary(branch_name: str, commit_info: dict | None, repo_path, output_path) -> str:
    lines = ["# Local Commit Summary", ""]
    if commit_info is None:
        lines.append("No commit was created (nothing was staged, or delivery was blocked).")
    else:
        status_after = get_git_status(repo_path)
        lines.append(f"**Branch:** `{branch_name}`")
        lines.append(f"**Commit hash:** `{commit_info['hash']}`")
        lines.append(f"**Commit message:** {commit_info['message']}")
        lines.append(f"**Author:** {commit_info['author']}")
        lines.append("")
        lines.append(f"## Files committed ({len(commit_info['files'])})")
        lines.extend(f"- `{f}`" for f in commit_info["files"])
        lines.append("")
        lines.append(f"**Working tree clean after commit:** {status_after['clean']}")

    content = "\n".join(lines) + "\n"
    Path(output_path).write_text(content, encoding="utf-8")
    return content


def generate_push_result(push_result: dict, remote_info: dict, output_path) -> str:
    lines = ["# Push Result", ""]
    lines.append(f"**Branch pushed:** `{push_result['branch']}`")
    lines.append(f"**Remote:** `{remote_info.get('fetch_url') or remote_info.get('push_url') or '(unknown)'}`")
    lines.append(f"**Success:** {push_result['success']}")
    lines.append("")
    lines.append("## Command output")
    lines.append("```")
    lines.append(push_result.get("stdout") or "(no stdout)")
    if push_result.get("stderr"):
        lines.append(push_result["stderr"])
    lines.append("```")
    if push_result["success"]:
        url = _github_web_url(remote_info.get("fetch_url") or remote_info.get("push_url") or "", push_result["branch"])
        if url:
            lines.append("")
            lines.append(f"**Probable GitHub branch URL:** {url}")

    content = "\n".join(lines) + "\n"
    Path(output_path).write_text(content, encoding="utf-8")
    return content


# ── Orchestrator ───────────────────────────────────────────────────────────────

def run_local_delivery(
    repo_path,
    mode: str,
    branch_name: str | None,
    commit_message: str | None,
    output_dir,
    sandbox_allowlist=None,
    plan_only: bool = False,
) -> dict:
    """
    End-to-end delivery workflow. Always writes delivery_safety_check.md,
    github_delivery_plan.md, repo_hygiene_report.md/.json, and delivery_state.json.
    Writes changed_files_report.md / local_commit_summary.md / push_result.md only
    when the corresponding step actually runs.

    mode: "local_only" | "sandbox_push"
    Returns the final delivery_state dict (also written to delivery_state.json).
    """
    repo_path = Path(repo_path).resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sandbox_allowlist = sandbox_allowlist or DEFAULT_SANDBOX_ALLOWLIST

    precheck = assert_clean_delivery_preconditions(repo_path, mode, branch_name, sandbox_allowlist)
    generate_delivery_safety_check(precheck, mode, branch_name, output_dir / "delivery_safety_check.md")
    generate_github_delivery_plan(precheck, mode, branch_name, output_dir / "github_delivery_plan.md")
    generate_repo_hygiene_report(precheck.get("repo_hygiene") or {}, output_dir)

    state = {
        "repo_path": str(repo_path),
        "mode": mode,
        "branch_name": branch_name,
        "repo_type": precheck["repo_type"],
        "decision": precheck["decision"],
        "block_reason": precheck.get("block_reason"),
        "repo_hygiene": precheck.get("repo_hygiene"),
        "plan_only": plan_only,
        "commit_hash": None,
        "files_committed": [],
        "push_attempted": False,
        "push_succeeded": None,
        "timestamp": time.time(),
    }

    if precheck["decision"] == "BLOCKED" or plan_only:
        if plan_only and precheck["decision"] != "BLOCKED":
            state["note"] = "delivery-plan-only run — no branch, commit, or push was performed"
        (output_dir / "delivery_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state

    if not branch_name or not commit_message:
        raise DeliveryError("branch_name and commit_message are required unless plan_only=True")

    create_local_delivery_branch(repo_path, branch_name)
    staged_result = stage_allowed_files(repo_path)

    generate_changed_files_report(repo_path, output_dir / "changed_files_report.md")

    commit_info = create_local_delivery_commit(repo_path, commit_message)
    if commit_info is None:
        state["decision"] = "BLOCKED"
        state["blocked_reason"] = "Nothing to commit — working tree already matched HEAD after denylist filtering."
        generate_local_commit_summary(branch_name, None, repo_path, output_dir / "local_commit_summary.md")
        (output_dir / "delivery_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state

    state["commit_hash"] = commit_info["hash"]
    state["files_committed"] = commit_info["files"]
    state["denied_files_excluded"] = staged_result["denied_removed"]
    generate_local_commit_summary(branch_name, commit_info, repo_path, output_dir / "local_commit_summary.md")

    if mode == "sandbox_push" and precheck["decision"] == "PASS_SANDBOX_PUSH":
        push_result = push_sandbox_branch(repo_path, branch_name)
        state["push_attempted"] = True
        state["push_succeeded"] = push_result["success"]
        generate_push_result(push_result, precheck["remote_info"], output_dir / "push_result.md")

    (output_dir / "delivery_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state
