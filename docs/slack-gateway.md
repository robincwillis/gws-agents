# Slack Gateway

Bridges Slack DMs and @mentions to the workspace_agents ADK server running locally. Uses Socket Mode — no public URL or port forwarding required.

## Prerequisites

- `adk` and `gws` installed and authenticated
- `slack_sdk`, `httpx`, and `python-dotenv` installed:
  ```bash
  pip install slack_sdk httpx python-dotenv
  ```

## Slack App Setup

### 1. Create the app

Go to https://api.slack.com/apps → **Create New App** → **From Scratch**

### 2. Enable Socket Mode

**Settings → Socket Mode** → Enable → generate an App-Level Token with the `connections:write` scope. This is your `SLACK_APP_TOKEN` (`xapp-...`).

### 3. Add bot scopes

**OAuth & Permissions → Bot Token Scopes**, add:

| Scope | Purpose |
|---|---|
| `chat:write` | Post and update messages |
| `im:history` | Read DMs |
| `im:write` | Open DM channels |
| `channels:history` | Read channel messages |
| `app_mentions:read` | Receive @mentions |

### 4. Subscribe to events

**Event Subscriptions → Enable → Subscribe to bot events**:

- `message.im` — direct messages
- `app_mention` — @mentions in channels

### 5. Install to workspace

**OAuth & Permissions → Install to Workspace** → copy the Bot User OAuth Token. This is your `SLACK_BOT_TOKEN` (`xoxb-...`).

## Configuration

Add both tokens to `.env` at the repo root:

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

## Running

Two terminals are required:

```bash
# Terminal 1 — ADK API server
adk api_server workspace_agents

# Terminal 2 — Slack gateway
python slack_gateway.py
```

## Usage

**From iPhone (or any Slack client):** DM the bot directly, or @mention it in a channel.

The bot posts `Thinking...` immediately on receipt, then updates the message with the agent's reply.

**In channels:** replies are threaded to keep conversations tidy.

**Commands:**

| Message | Effect |
|---|---|
| `/reset` or `reset` | Clear the current session and start fresh |
