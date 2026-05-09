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
  Terminal 1:  adk api_server workspace_agents
  Terminal 2:  python slack_gateway.py

Commands (send as a DM to the bot):
  /reset   — start a new conversation session
"""

import json
import os
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
SLACK_MAX_CHARS = 3000  # safe Slack message length

# Slack user_id → ADK session_id (in-memory; resets on gateway restart)
_sessions: dict[str, str] = {}


def _ensure_session(user_id: str) -> str:
    if user_id not in _sessions:
        session_id = uuid.uuid4().hex
        _sessions[user_id] = session_id
        httpx.post(
            f"{ADK_BASE}/apps/{APP_NAME}/users/{user_id}/sessions/{session_id}",
            json={},
            timeout=10,
        )
    return _sessions[user_id]


def _reset_session(user_id: str) -> None:
    _sessions.pop(user_id, None)


def _run_agent(user_id: str, text: str) -> str:
    session_id = _ensure_session(user_id)
    resp = httpx.post(
        f"{ADK_BASE}/run",
        json={
            "app_name": APP_NAME,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": text}]},
        },
        timeout=300,  # workspace ops can be slow
    )
    resp.raise_for_status()
    for event in reversed(resp.json()):
        content = event.get("content", {})
        if content.get("role") != "model":
            continue
        for part in content.get("parts", []):
            if t := part.get("text"):
                return t
    return "(no response)"


def _truncate(text: str) -> str:
    if len(text) <= SLACK_MAX_CHARS:
        return text
    return text[: SLACK_MAX_CHARS - 40] + "\n\n_(response truncated — ask for more)_"


def _handle(client: SocketModeClient, req: SocketModeRequest) -> None:
    # Acknowledge immediately to avoid Slack retries
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

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
        reply = _truncate(_run_agent(user_id, text))
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
