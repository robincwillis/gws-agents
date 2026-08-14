"""
Slack Socket Mode gateway for workspace_agents.

Bridges Slack DMs and @mentions to the ADK api_server running locally.
Uses Socket Mode — no public URL or port forwarding required.

Setup:
  1. Go to https://api.slack.com/apps → Create New App → From Scratch
  2. Settings → Socket Mode → Enable, generate App-Level Token (connections:write scope)
     → this is your SLACK_APP_TOKEN (xapp-...)
  3. OAuth & Permissions → Bot Token Scopes: chat:write, im:history, im:write,
     channels:history, app_mentions:read
  4. Event Subscriptions → Enable → Subscribe to bot events:
       message.im  (DMs)
       app_mention (channel @mentions)
  5. Install to Workspace → copy Bot User OAuth Token
     → this is your SLACK_BOT_TOKEN (xoxb-...)
  6. Add to .env (at the repo root):
       SLACK_BOT_TOKEN=xoxb-...
       SLACK_APP_TOKEN=xapp-...

Run (two terminals):
  Terminal 1:  uv run adk api_server workspace_agents
  Terminal 2:  uv run python slack_gateway.py

Commands (send as a DM to the bot):
  /reset   — start a new conversation session
"""

import json
import os
import re
import uuid
from threading import Event

import httpx
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

load_dotenv()

ADK_BASE = os.getenv("ADK_BASE_URL", "http://127.0.0.1:8000")
APP_NAME = "workspace_agents"
# Slack hard limit per chat.update is ~40k chars. 12k keeps responses readable
# without dropping much; long agent outputs over this still get truncated, but
# the truncation now closes any open ``` fence so tables don't render broken.
SLACK_MAX_CHARS = 12000

# Slack user_id → ADK session_id (in-memory; resets on gateway restart)
_sessions: dict[str, str] = {}

# Bounded LRU for Slack event_ids we've already handled. Socket Mode occasionally
# redelivers the same event after transient hiccups (or after our handler
# returned an error to ADK on the first pass). Dedupe so the user doesn't see
# two replies — one error and one success — for a single message.
_seen_event_ids: dict[str, None] = {}
_SEEN_LIMIT = 256


def _ensure_session(user_id: str) -> str:
    if user_id not in _sessions:
        session_id = uuid.uuid4().hex
        resp = httpx.post(
            f"{ADK_BASE}/apps/{APP_NAME}/users/{user_id}/sessions/{session_id}",
            json={},
            timeout=10,
        )
        resp.raise_for_status()
        # Only cache once the server has confirmed the session exists — caching
        # optimistically here would leave a dead session_id in _sessions forever
        # (never re-created) if this request failed.
        _sessions[user_id] = session_id
    return _sessions[user_id]


def _reset_session(user_id: str) -> None:
    _sessions.pop(user_id, None)


def _post_run(user_id: str, session_id: str, text: str) -> httpx.Response:
    return httpx.post(
        f"{ADK_BASE}/run",
        json={
            "app_name": APP_NAME,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": text}]},
        },
        timeout=300,  # workspace ops can be slow
    )


def _run_agent(user_id: str, text: str) -> str:
    session_id = _ensure_session(user_id)
    resp = _post_run(user_id, session_id, text)
    if resp.status_code == 404:
        # The cached session_id is gone server-side (e.g. api_server was
        # restarted with a different/fresh session store since we created it).
        # Recreate the session and retry once instead of 404ing on every
        # message from this user until the gateway itself is restarted.
        _reset_session(user_id)
        session_id = _ensure_session(user_id)
        resp = _post_run(user_id, session_id, text)
    resp.raise_for_status()
    for event in reversed(resp.json()):
        content = event.get("content", {})
        if content.get("role") != "model":
            continue
        for part in content.get("parts", []):
            if t := part.get("text"):
                return t
    return "(no response)"


# ── Slack mrkdwn sanitizer ────────────────────────────────────────────────
# The agent's system prompt steers it toward Slack/terminal-friendly output,
# but LLMs slip occasionally. These transforms catch the common slips so the
# user sees rendered formatting instead of literal `**asterisks**` and broken
# `| pipe | tables |`.

_FENCE_OR_INLINE_CODE_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_MD_TABLE_RE = re.compile(
    r"^\|[^\n]*\|[ \t]*\n"          # header row
    r"^\|[\s\-:|]+\|[ \t]*\n"       # separator row of dashes/colons/pipes
    r"(?:^\|[^\n]*\|[ \t]*\n?)+",   # one or more body rows
    re.MULTILINE,
)


def _md_table_to_ascii(match: re.Match) -> str:
    block = match.group(0).strip("\n")
    rows = []
    for ln in block.splitlines():
        ln = ln.strip().strip("|")
        if not ln:
            continue
        rows.append([c.strip() for c in ln.split("|")])
    if len(rows) < 2:
        return match.group(0)
    is_separator = all(set(c) <= set("-:") for c in rows[1] if c)
    header = rows[0]
    body = rows[2:] if is_separator else rows[1:]
    n_cols = len(header)
    body = [(r + [""] * n_cols)[:n_cols] for r in body]
    widths = [max(len(header[i]), *(len(r[i]) for r in body), 1) for i in range(n_cols)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    sep = "  ".join("-" * w for w in widths)
    lines = [fmt.format(*header), sep] + [fmt.format(*r) for r in body]
    return "```\n" + "\n".join(lines) + "\n```"


def _sanitize_for_slack(text: str) -> str:
    """Best-effort cleanup: rewrite markdown the LLM may have emitted into Slack mrkdwn."""
    parts = _FENCE_OR_INLINE_CODE_RE.split(text)
    # Even indices are prose, odd are code (preserved as-is).
    for i in range(0, len(parts), 2):
        seg = parts[i]
        seg = _MD_TABLE_RE.sub(_md_table_to_ascii, seg)
        seg = _BOLD_RE.sub(r"*\1*", seg)
        seg = _HEADING_RE.sub(r"*\2*", seg)
        parts[i] = seg
    return "".join(parts)


def _truncate(text: str) -> str:
    if len(text) <= SLACK_MAX_CHARS:
        return text
    cut = text[: SLACK_MAX_CHARS - 60]
    # If the cut lands inside an open ``` fence, close it so the truncation
    # marker doesn't get swallowed into a code block (and the prose before it
    # doesn't render as monospace).
    if cut.count("```") % 2 == 1:
        cut = cut.rstrip() + "\n```"
    return cut + "\n\n_(response truncated — ask for more)_"


def _seen(event_id: str | None) -> bool:
    """Return True if event_id has been handled before; record it otherwise."""
    if not event_id:
        return False
    if event_id in _seen_event_ids:
        return True
    _seen_event_ids[event_id] = None
    if len(_seen_event_ids) > _SEEN_LIMIT:
        # Drop the oldest entry — dict preserves insertion order.
        _seen_event_ids.pop(next(iter(_seen_event_ids)))
    return False


def _handle(client: SocketModeClient, req: SocketModeRequest) -> None:
    # Acknowledge immediately to avoid Slack retries
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    # Slack Socket Mode can redeliver the same event after transient errors.
    # Dedupe by event_id so a single user message never produces two replies.
    if _seen(req.payload.get("event_id")):
        return

    event = req.payload.get("event", {})
    event_type = event.get("type")

    if event_type not in ("message", "app_mention"):
        return
    if event.get("bot_id") or event.get("subtype"):
        return

    user_id = event.get("user")
    channel = event.get("channel")
    ts = event.get("ts")
    thread_ts = event.get("thread_ts")
    text = (event.get("text") or "").strip()

    if not text or not user_id:
        return

    web: WebClient = client.web_client

    # Strip @mention prefix for channel events
    if event_type == "app_mention":
        bot_id = web.auth_test()["user_id"]
        text = text.replace(f"<@{bot_id}>", "").strip()

    # /reset command
    if text.lower() in ("/reset", "reset"):
        _reset_session(user_id)
        web.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts or ts,
            text="Session cleared — starting fresh.",
        )
        return

    # In channels, reply in-thread; in DMs, no forced threading
    is_dm = event.get("channel_type") == "im"
    reply_thread = None if is_dm else (thread_ts or ts)

    placeholder = web.chat_postMessage(
        channel=channel,
        thread_ts=reply_thread,
        text="_Thinking..._",
    )

    try:
        reply = _truncate(_sanitize_for_slack(_run_agent(user_id, text)))
    except httpx.ConnectError:
        reply = "Cannot reach the ADK server — is `adk api_server workspace_agents` running?"
    except httpx.TimeoutException:
        reply = "The agent timed out. Try a more specific request."
    except Exception as exc:
        reply = f"Error: {exc}"

    web.chat_update(channel=channel, ts=placeholder["ts"], text=reply)


if __name__ == "__main__":
    for var in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        if not os.environ.get(var):
            raise SystemExit(f"Missing env var: {var} — see setup instructions at the top of this file")

    web_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    socket_client = SocketModeClient(
        app_token=os.environ["SLACK_APP_TOKEN"],
        web_client=web_client,
    )
    socket_client.socket_mode_request_listeners.append(_handle)
    socket_client.connect()
    print(f"Slack gateway connected — forwarding to {ADK_BASE}")
    Event().wait()
