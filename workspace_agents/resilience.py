"""App-level guardrails: keep a session under the model's context window, and
turn model/tool failures (token-limit overflows, API errors, network drops)
into a chat message instead of an unhandled exception that kills `adk run`.
"""

from __future__ import annotations

import sys
import traceback

from google.adk.plugins import BasePlugin
from google.adk.models.llm_response import LlmResponse
from google.genai import types

_RED = "\033[91m"
_DIM = "\033[2m"
_RESET = "\033[0m"


class WorkspaceAgentsResiliencePlugin(BasePlugin):
    """Catches on_model_error / on_tool_error so one failure doesn't take down
    the whole interactive session — it's reported to the user and the agent
    loop keeps running.
    """

    def __init__(self, name: str = "workspace_agents_resilience"):
        super().__init__(name=name)

    async def on_model_error_callback(self, *, callback_context, llm_request, error):
        agent_name = callback_context.agent_name
        print(
            f"\n{_RED}[model error]{_RESET} {_DIM}{agent_name}: {error}{_RESET}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exception(error, file=sys.stderr)
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text=(
                    f"⚠️ {agent_name} hit an error calling the model and had to stop "
                    f"this turn: {error}\n\nThe session is still alive — you can retry, "
                    f"narrow the request (smaller batches, a date range, a lower "
                    f"maxResults), or ask system_diagnostics to check upstream health."
                ))],
            ),
        )

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        name = getattr(tool, "name", str(tool))
        print(
            f"\n{_RED}[tool error]{_RESET} {_DIM}{name}: {error}{_RESET}",
            file=sys.stderr,
            flush=True,
        )
        return {"error": f"{name} raised {type(error).__name__}: {error}"}
