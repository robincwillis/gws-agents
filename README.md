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

## Setup Instructions

### 1. Prerequisites
- **Google Workspace CLI (`gws`)** installed and authenticated.
- **Agent Development Kit (`adk`)** installed.

### 2. Configuration
Update the `.env` file in this directory with your Google API Key:

```bash
# workspace_agents/.env
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=YOUR_ACTUAL_API_KEY
```

### 3. Running the Suite (with uv)
Running with `uv` ensures a controlled, reproducible environment:

```bash
# Set up a venv and install dependencies
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Run the root agent directly
uv run workspace_agents/agent.py
```

Alternatively, if you've installed the `google-adk` globally:

```bash
adk run workspace_agents
```

You can then issue commands to the orchestrator:
- *"Analyze my Drive root and propose a reorganization."*
- *"Find all files over 50MB and move them to archive."*
- *"Clean up my Promotions tab."*

---

## Tooling & Integration
The agents use the following internal Python tools to bridge with the CLI:
- `gws_cli(command)`: Executes any arbitrary `gws` command.
- `gws_schema(resource_method)`: Inspects API schemas to ensure correct parameter usage.

## Folder Structure
- `agent.py`: Main entry point and agent definitions.
- `skills/`: Individual agent system prompts in Markdown.
- `.env`: Environment variables and secrets.
