# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Suite

```bash
adk run workspace_agents --use_local_storage
```

`--use_local_storage` persists sessions to `.adk/` (already gitignored) so interrupted runs can be resumed. On restart, ADK automatically reloads the previous session — no extra flags needed.

To start a fresh session, delete `.adk/` or pass `--session_service_uri memory://` to force in-memory.

Requires both `gws` (Google Workspace CLI) and `adk` (Agent Development Kit) installed and authenticated. Set your API key in `.env` at the repo root (see `.env.example`):

```
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=<your-key>
```

## Architecture

This is a Python multi-agent system using Google's Agent Development Kit (ADK) with the Gemini model.

**Entry point:** `workspace_agents/agent.py`

**Pattern:** A `root_agent` orchestrator delegates to four specialized sub-agents based on user intent. All agents share a single `McpToolset` connected to the `workspace-developer` MCP server via SSE at `https://workspace-developer.goog/mcp`. Tools are typed with defined schemas and invoked directly — no CLI string construction needed.

**Agents:**
| Agent | Name in code | Responsibility |
|---|---|---|
| Storage Sentinel | `storage_sentinel` | Find files/attachments >25MB, generate deletion manifest, requires "PURGE" to delete |
| Auction Intelligence | `auction_intelligence` | Extract real estate bid data from `label:auction` emails → `~/auctions/pricing_trends.json` |
| Inbox Gardener | `inbox_gardener` | Unsubscribe from senders not opened in 14 days, triage receipts to 'Financials' |
| Drive Architect | `drive_architect` | Audit Drive root, output JSON move plan, requires approval before executing |

**Safety pattern:** Destructive operations (file deletion, Drive moves) are two-phase — agents generate a manifest/plan first and wait for explicit user confirmation before executing.

**Agent system prompts** live in `workspace_agents/skills/` as Markdown files (one per agent — `architect.md`, `auction.md`, `gardener.md`, `organizer.md`, `sentinel.md`).

**gws skill packs** (`gws-*/`, `persona-*/`, `recipe-*/` subdirectories of `workspace_agents/skills/`) are not committed. Install/refresh them with:

```bash
cd workspace_agents && gws generate-skills
```

`prompt_builder.py` composes each agent's full system prompt by concatenating its base `.md` file with the relevant `gws-*/SKILL.md` reference packs (see `SKILLS_MAP`).
