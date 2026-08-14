"""System diagnostic tools — verify every upstream service this suite depends on.

Each `check_*` returns a single line beginning with `OK:`, `WARN:`, or `FAIL:`.
The `system_diagnostics` agent calls all of them and renders a status table.
"""

import json
import os
import subprocess
from pathlib import Path


def check_gws_auth() -> str:
    """Verify the gws CLI is authenticated and the OAuth token is valid."""
    result = subprocess.run(
        "gws auth status",
        shell=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    out = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        return f"FAIL: gws auth status exited {result.returncode}: {out[:200]}"
    try:
        info = json.loads(out)
    except json.JSONDecodeError:
        return f"OK: authenticated ({out.splitlines()[0][:80]})"
    if not info.get("encrypted_credentials_exists") and not info.get("plain_credentials_exists"):
        return "FAIL: gws credentials missing — run `gws auth login`"
    if not info.get("has_refresh_token", True):
        return "WARN: no refresh_token — re-authenticate with `gws auth login`"
    project = info.get("project_id") or "?"
    n_scopes = info.get("scope_count", 0)
    return f"OK: project={project}, {n_scopes} scopes, refresh_token present"


async def check_mcp_docs() -> str:
    """Verify the workspace-developer MCP server is reachable and lists tools."""
    # Local import: agent.py imports diagnostics, so we avoid the cycle.
    from agent import docs_toolset

    try:
        tools = await docs_toolset.get_tools()
        names = ", ".join(getattr(t, "name", "?") for t in tools)
        return f"OK: {len(tools)} tools ({names})"
    except Exception as exc:
        return f"FAIL: {type(exc).__name__}: {str(exc)[:200]}"


def check_gemini_api_key() -> str:
    """Verify the credentials used by Gemini model calls."""
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "0") == "1":
        return "OK: GOOGLE_GENAI_USE_VERTEXAI=1 (using Vertex AI ADC, not API key)"
    key = os.getenv("GOOGLE_API_KEY", "")
    if not key:
        return "FAIL: GOOGLE_API_KEY not set (check .env at repo root)"
    return f"OK: API key present (length={len(key)})"


def check_adk_session_storage() -> str:
    """Verify the local .adk/ session storage is writable."""
    candidates = [Path("workspace_agents/.adk"), Path(".adk")]
    for path in candidates:
        if not path.exists():
            continue
        if not os.access(path, os.W_OK):
            return f"FAIL: {path} exists but is not writable"
        db = path / "session.db"
        detail = f"{db.stat().st_size} bytes" if db.exists() else "no session.db yet"
        return f"OK: {path}/ writable ({detail})"
    return "WARN: no .adk/ directory yet (created on first run with --use_local_storage)"


def check_slack_tokens() -> str:
    """Verify Slack tokens, if the slack_gateway is in use."""
    bot = os.getenv("SLACK_BOT_TOKEN", "")
    app = os.getenv("SLACK_APP_TOKEN", "")
    if not bot and not app:
        return "OK: SLACK_*_TOKEN unset — gateway not in use"
    missing = [n for n, v in (("SLACK_BOT_TOKEN", bot), ("SLACK_APP_TOKEN", app)) if not v]
    if missing:
        return f"FAIL: partial Slack config — missing {', '.join(missing)}"
    bot_prefix = "xoxb-" if bot.startswith("xoxb-") else "unexpected prefix"
    app_prefix = "xapp-" if app.startswith("xapp-") else "unexpected prefix"
    return f"OK: SLACK_BOT_TOKEN ({bot_prefix}), SLACK_APP_TOKEN ({app_prefix})"


DIAGNOSTIC_TOOLS = [
    check_gws_auth,
    check_mcp_docs,
    check_gemini_api_key,
    check_adk_session_storage,
    check_slack_tokens,
]
