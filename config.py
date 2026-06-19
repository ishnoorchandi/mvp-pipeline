"""
MVP Pipeline — Central Configuration
=====================================
All secrets are read from environment variables.
Copy .env.example to .env and fill in your values before running.

Required env vars:
  OPENAI_API_KEY
  DEEPSEEK_API_KEY
  JIRA_EMAIL
  JIRA_API_TOKEN
  JIRA_BASE_URL

Optional env vars (have safe defaults):
  DB_USER   (default: current OS user via $USER)
  DB_PASS   (default: empty)
"""

import os
from pathlib import Path

# ── Load .env if present (python-dotenv is optional but recommended) ──────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv not installed; rely on shell environment


def _require(name: str) -> str:
    """Return env var value, or raise a clear error if missing."""
    val = os.getenv(name, "").strip()
    if not val:
        raise EnvironmentError(
            f"\n  Missing required environment variable: {name}\n"
            f"  Add it to your .env file or export it in your shell.\n"
            f"  See .env.example for all required variables.\n"
        )
    return val


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ── Directories ────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent.resolve()
RUNS_DIR  = BASE_DIR / "runs"
SMOKE_DIR = BASE_DIR / "smoke_checks"

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = _require("OPENAI_API_KEY")
GPT_MODEL      = "gpt-4o-mini"
GPT4O_MODEL    = "gpt-4o"        # used for legal/privacy governance review

# ── Jira ──────────────────────────────────────────────────────────────────────
JIRA_BASE_URL  = _require("JIRA_BASE_URL")
JIRA_EMAIL     = _require("JIRA_EMAIL")
JIRA_API_TOKEN = _require("JIRA_API_TOKEN")

# ── DeepSeek API ──────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY  = _require("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL    = "deepseek-chat"

# ── Claude Code ───────────────────────────────────────────────────────────────
CLAUDE_CODE_CMD = ["claude", "-p", "--dangerously-skip-permissions"]
CLAUDE_TIMEOUT  = 600   # seconds per Claude Code invocation

# ── Pipeline behaviour ────────────────────────────────────────────────────────
MAX_FIX_ITERATIONS        = 3  # max Claude Code quality fix cycles
MAX_GOVERNANCE_ITERATIONS = 2  # max Claude Code governance fix cycles

# ── Backend ───────────────────────────────────────────────────────────────────
BACKEND_PORT = 5001
BACKEND_HOST = "127.0.0.1"

# ── Database ──────────────────────────────────────────────────────────────────
DB_NAME = "mvp_pipeline_db"
DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = _optional("DB_USER", os.getenv("USER", ""))
DB_PASS = _optional("DB_PASS", "")

# ── File extensions collected for code review ─────────────────────────────────
CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".sql",
             ".json", ".yaml", ".yml", ".env.example", ".md")
