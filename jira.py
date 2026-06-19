"""
MVP Pipeline — Jira Integration
=================================
Fetches a Jira issue and formats it as clean markdown
for the pipeline's raw_input.

Usage (standalone test):
    python jira.py PROJ-123

Used by pipeline_mvp_builder.py via --jira PROJ-123
"""

import base64
import json
import sys
from pathlib import Path

import requests

from config import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN


# ── Auth ──────────────────────────────────────────────────────────────────────

def _auth_header() -> dict:
    token = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    }


# ── ADF → plain text ──────────────────────────────────────────────────────────
# Jira descriptions use Atlassian Document Format (ADF), a nested JSON structure.
# This flattens it to readable plain text.

def _adf_to_text(node: dict | list | str | None, depth: int = 0) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(n, depth) for n in node)

    node_type = node.get("type", "")
    content   = node.get("content", [])
    text      = node.get("text", "")

    if node_type == "text":
        marks = {m["type"] for m in node.get("marks", [])}
        t = text
        if "code" in marks:
            t = f"`{t}`"
        return t

    if node_type in ("paragraph", "blockquote"):
        return _adf_to_text(content, depth) + "\n"

    if node_type == "heading":
        level = node.get("attrs", {}).get("level", 2)
        return "#" * level + " " + _adf_to_text(content, depth) + "\n"

    if node_type == "bulletList":
        return "".join(_adf_to_text(item, depth) for item in content)

    if node_type == "orderedList":
        lines = []
        for i, item in enumerate(content, 1):
            lines.append(f"{i}. {_adf_to_text(item.get('content', []), depth).strip()}")
        return "\n".join(lines) + "\n"

    if node_type == "listItem":
        indent = "  " * depth
        inner  = _adf_to_text(content, depth + 1).strip()
        return f"{indent}- {inner}\n"

    if node_type == "codeBlock":
        lang  = node.get("attrs", {}).get("language", "")
        inner = _adf_to_text(content, depth)
        return f"```{lang}\n{inner}\n```\n"

    if node_type == "rule":
        return "\n---\n"

    if node_type == "hardBreak":
        return "\n"

    if node_type == "doc":
        return _adf_to_text(content, depth)

    # Fallback: just recurse into content
    return _adf_to_text(content, depth)


def _parse_description(desc: dict | str | None) -> str:
    if desc is None:
        return "(no description)"
    if isinstance(desc, str):
        return desc
    # ADF object
    return _adf_to_text(desc).strip()


# ── Field extraction ──────────────────────────────────────────────────────────

def _extract_acceptance_criteria(fields: dict) -> str:
    """
    Tries common Jira custom field names for acceptance criteria.
    Returns plain text or empty string if not found.
    """
    candidates = [
        "customfield_10016",  # common in many Jira setups
        "customfield_10014",
        "customfield_10020",
        "acceptance_criteria",
    ]
    for key in candidates:
        val = fields.get(key)
        if val:
            if isinstance(val, dict):
                return _adf_to_text(val).strip()
            if isinstance(val, str):
                return val.strip()
    return ""


# ── Main fetch ────────────────────────────────────────────────────────────────

def fetch_issue(issue_key: str) -> dict:
    """Fetch raw Jira issue JSON."""
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    resp = requests.get(url, headers=_auth_header(), timeout=15)
    if resp.status_code == 401:
        raise PermissionError("Jira auth failed. Check JIRA_EMAIL and JIRA_API_TOKEN in config.py.")
    if resp.status_code == 404:
        raise FileNotFoundError(f"Issue {issue_key} not found.")
    resp.raise_for_status()
    return resp.json()


def format_issue_as_mvp_input(issue_key: str) -> str:
    """
    Fetch a Jira issue and return it as clean markdown
    ready to be fed into the MVP pipeline as raw_input.
    """
    data   = fetch_issue(issue_key)
    fields = data.get("fields", {})

    summary     = fields.get("summary", "(no summary)")
    issue_type  = fields.get("issuetype", {}).get("name", "")
    priority    = fields.get("priority", {}).get("name", "")
    status      = fields.get("status", {}).get("name", "")
    reporter    = (fields.get("reporter") or {}).get("displayName", "")
    labels      = ", ".join(fields.get("labels", [])) or "none"
    description = _parse_description(fields.get("description"))
    acceptance  = _extract_acceptance_criteria(fields)

    lines = [
        f"# Jira Issue: {issue_key}",
        f"**Summary:** {summary}",
        f"**Type:** {issue_type}  |  **Priority:** {priority}  |  **Status:** {status}",
        f"**Reporter:** {reporter}  |  **Labels:** {labels}",
        "",
        "## Description",
        description,
    ]

    if acceptance:
        lines += ["", "## Acceptance Criteria", acceptance]

    # Pull in any linked issues as context (just keys + summaries)
    links = fields.get("issuelinks", [])
    if links:
        lines += ["", "## Linked Issues"]
        for link in links[:5]:  # cap at 5
            outward = link.get("outwardIssue") or link.get("inwardIssue")
            if outward:
                rel    = link.get("type", {}).get("name", "")
                lkey   = outward.get("key", "")
                lsum   = outward.get("fields", {}).get("summary", "")
                lines.append(f"- [{lkey}] {rel}: {lsum}")

    return "\n".join(lines)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python jira.py PROJ-123")
        sys.exit(1)

    key = sys.argv[1].upper()
    print(f"Fetching {key}...\n")
    try:
        result = format_issue_as_mvp_input(key)
        print(result)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
