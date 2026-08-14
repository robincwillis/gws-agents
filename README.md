# Google Workspace Multi-Agent Suite (2026)

A persistent multi-agent system built using the **Agent Development Kit (ADK)** and the **Google Workspace CLI (gws)**. This suite provides four specialized agents to automate storage management, data extraction, inbox triage, and Drive organization.

## Architecture Overview

The suite is managed by a `root_agent` that orchestrates four specialized sub-agents. Each agent is equipped with tools to execute `gws` commands directly, allowing them to interact with Google Drive, Gmail, and other Workspace services.

### Core Agents

| Agent | Frequency | Goal |
| :--- | :--- | :--- |
| **Storage Sentinel** | Low | Identify and archive files/attachments > 25MB. |
| **Auction Intelligence** | Medium | Scrape pricing data from auction emails into structured JSON. |
| **Inbox Gardener** | High | Mass-unsubscribe from clutter and triage receipts/financials. |
| **Drive Architect** | One-off | Audit the Drive root and propose a reorganization plan. |

---

## Agent Definitions

### 1. Storage Sentinel
**Goal:** Identify large files/attachments, download them with metadata, and queue them for deletion.
- **Logic:** Uses `gws drive files list` with size filters and `gws gmail messages list` for large attachments.
- **Safety:** Never deletes without explicit "PURGE" approval.

### 2. Auction Intelligence Agent
**Goal:** Scrape pricing data from automated auction emails.
- **Logic:** Monitors `label:auction`. Extracts `Property_Address`, `Current_Bid`, `Auction_End_Date`, and `Source_Site`.
- **Storage:** Appends to `~/auctions/pricing_trends.json`.

### 3. Inbox Gardener
**Goal:** Mass-unsubscribe and triage.
- **Logic:** Leverages `gws gmail +unsubscribe` (RFC 8058).
- **Rule:** Unsubscribe if not opened in 14 days. Triage one-time receipts to 'Financials'.

### 4. Drive Architect
**Goal:** Audit and reorganize the "Root" folder mess.
- **Logic:** Runs `gws drive files list --page-all` to build a move tree.
- **Output:** Generates a JSON 'Move Plan' for user approval.

---

## Setup

### 1. Prerequisites
- **Google Workspace CLI (`gws`)** — installed and authenticated.
- **Agent Development Kit (`adk`)** — installed (`pip install google-adk`).

#### Re-authenticating gws

OAuth tokens expire (refresh tokens after ~7 days for unverified apps in test mode, sooner if revoked). If agents start failing with `401 Unauthorized` or `invalid_grant`, refresh credentials:

```bash
gws auth status         # check current state
gws auth login          # re-run the OAuth flow (opens browser)
```

Other useful subcommands:
- `gws auth login --readonly` — request read-only scopes only.
- `gws auth login -s drive,gmail,sheets` — limit the scope picker to specific services.
- `gws auth logout` — clear saved credentials before re-authenticating from scratch.

### 2. Configuration
Copy `.env.example` to `.env` at the repo root and fill in your values:

```bash
cp .env.example .env
# then edit .env — at minimum set GOOGLE_API_KEY
```

Key vars:
- `GOOGLE_API_KEY` — from https://aistudio.google.com/apikey
- `GOOGLE_GENAI_USE_VERTEXAI` — `0` for the public Gemini API, `1` to use Vertex AI (requires `gcloud auth application-default login`)
- `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` — only needed if running `slack_gateway.py`

### 3. Install gws skills
The agents rely on `gws-*`, `persona-*`, and `recipe-*` skill packs that ship with the `gws` CLI. They are **not committed to this repo** — install them locally so they stay current:

```bash
cd workspace_agents
gws generate-skills
```

This populates `workspace_agents/skills/` with the latest skill set and writes an index at `workspace_agents/docs/skills.md`. Re-run any time you upgrade `gws` to refresh.

---

## Running the Suite

The primary entry point is `adk run`, invoked through `uv run` so it always resolves to this project's `.venv` deps rather than any global/Homebrew `adk`:

```bash
uv run adk run workspace_agents --use_local_storage
```

`--use_local_storage` persists sessions to `workspace_agents/.adk/` (gitignored) so interrupted runs can be resumed. ADK reloads the previous session automatically on restart.

To start a fresh session, delete `workspace_agents/.adk/` or pass `--session_service_uri memory://` to force in-memory.

You can then issue commands to the orchestrator:
- *"Analyze my Drive root and propose a reorganization."*
- *"Find all files over 50MB and move them to archive."*
- *"Clean up my Promotions tab."*

### HTTP API server (`adk api_server`)

For programmatic access — e.g. the bundled `slack_gateway.py` or any other HTTP client — run the FastAPI server instead of the interactive CLI:

```bash
uv run adk api_server workspace_agents --use_local_storage
# default bind: http://127.0.0.1:8000
```

Endpoints used by `slack_gateway.py`:
- `POST /apps/workspace_agents/users/<user_id>/sessions/<session_id>` — create a session
- `POST /run` — submit a turn, returns the full event list

### Web UI with live traces (`adk web`)

`adk web` is a drop-in replacement for `adk api_server` (same endpoints) plus a browser UI at `http://127.0.0.1:8000` that shows a per-session trace tree: model calls, tool calls/results, sub-agent transfers, and latencies. Best tool for debugging an agent flow.

```bash
uv run adk web workspace_agents --use_local_storage
```

The slack gateway will keep working unchanged against either server.

### Verbose / debug logging

All three commands (`adk run`, `adk api_server`, `adk web`) accept the same logging flags:

```bash
uv run adk api_server -v workspace_agents --use_local_storage      # shortcut for --log_level debug
uv run adk api_server --log_level debug workspace_agents           # equivalent
```

`-v` / `--log_level debug` surfaces every HTTP request, MCP session event, model send/receive, and tool call/result.

If full DEBUG is too noisy, target individual subsystems from `workspace_agents/agent.py`:

```python
import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("google.adk.tools.mcp_tool").setLevel(logging.DEBUG)
logging.getLogger("google.adk.flows.llm_flows").setLevel(logging.DEBUG)
```

For full distributed traces (OpenTelemetry → Google Cloud Trace), add `--trace_to_cloud` or `--otel_to_cloud` (both require GCP ADC: `gcloud auth application-default login`).

### Development & testing (uv)

For iterating on agent code or running scripts in a controlled, reproducible environment:

```bash
# Set up a venv and install dependencies
uv venv
source .venv/bin/activate
uv pip install -r workspace_agents/requirements.txt

# Run the agent module directly (bypasses adk's CLI)
uv run workspace_agents/agent.py
```

---

## Tooling & Integration
The agents use the following internal Python tools to bridge with the CLI:
- `gws_cli(command)`: Executes any arbitrary `gws` command.
- `gws_schema(resource_method)`: Inspects API schemas to ensure correct parameter usage.

## Folder Structure
- `workspace_agents/agent.py` — main entry point and agent definitions.
- `workspace_agents/skills/` — agent system prompts (`*.md`) plus generated `gws-*/persona-*/recipe-*` packs (gitignored, install via `gws generate-skills`).
- `.env` — environment variables and secrets (at repo root, gitignored; see `.env.example`).
- `workspace_agents/.adk/` — local session storage (gitignored).
