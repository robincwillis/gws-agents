import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

# Force immediate exit on Ctrl+C — asyncio swallows KeyboardInterrupt mid-request
signal.signal(signal.SIGINT, lambda _sig, _frame: os._exit(0))
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from prompt_builder import build_skill

# ---------------------------------------------------------------------------
# Colored tool-call logging
# ---------------------------------------------------------------------------

_CYAN   = "\033[96m"
_YELLOW = "\033[93m"
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"

# ---------------------------------------------------------------------------
# Thinking spinner — runs in a background thread while the model is working
# ---------------------------------------------------------------------------

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_spinner_stop  = threading.Event()
_spinner_thread: threading.Thread | None = None

def _spin(agent_name: str) -> None:
    i = 0
    while not _spinner_stop.is_set():
        frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
        print(f"\r{_CYAN}{frame}{_RESET} {_DIM}{agent_name} thinking…{_RESET}",
              end="", file=sys.stderr, flush=True)
        _spinner_stop.wait(0.1)
        i += 1
    # Clear the spinner line when done
    print(f"\r{' ' * 40}\r", end="", file=sys.stderr, flush=True)

def _before_model(callback_context, llm_request):
    global _spinner_thread
    _spinner_stop.clear()
    _spinner_thread = threading.Thread(
        target=_spin, args=(callback_context.agent_name,), daemon=True
    )
    _spinner_thread.start()

def _after_model(callback_context, llm_response):
    _spinner_stop.set()
    if _spinner_thread:
        _spinner_thread.join(timeout=0.5)

def _before_tool(tool, args, tool_context):
    # Stop spinner before printing tool call (tool calls happen mid-turn)
    _spinner_stop.set()
    if _spinner_thread:
        _spinner_thread.join(timeout=0.5)
    name = getattr(tool, "name", str(tool))
    args_str = json.dumps(args, default=str)
    print(f"\n{_CYAN}[tool call]{_RESET} {_YELLOW}{name}{_RESET} {_DIM}{args_str}{_RESET}", file=sys.stderr, flush=True)

def _after_tool(tool, args, tool_context, tool_response):
    name = getattr(tool, "name", str(tool))
    preview = str(tool_response)
    is_error = preview.lstrip().startswith("Error")
    color = _RED if is_error else _GREEN
    tag = "[tool error]" if is_error else "[tool done]"
    if len(preview) > 200:
        preview = preview[:200] + "…"
    print(f"{color}{tag}{_RESET} {_YELLOW}{name}{_RESET} {_DIM}{preview}{_RESET}", file=sys.stderr, flush=True)

def _token() -> str:
    raw = json.loads((Path.home() / ".gemini" / "oauth_creds.json").read_text())
    return raw.get("access_token", "")

_MAX_OUTPUT_BYTES = 32_000
_DEFAULT_ARCHIVE_DIR = str(Path.home() / "archive")
_jobs: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Core execution tool
# ---------------------------------------------------------------------------

def gws_cli(command: str) -> str:
    """Execute a Google Workspace CLI (gws) command and return the output.

    Args:
        command: The gws subcommand and arguments, e.g. 'gmail messages list --params {"maxResults": 10}'
    """
    full_cmd = command if command.startswith("gws ") else f"gws {command}"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        output = result.stdout
    else:
        parts = [f"Error (exit {result.returncode}):"]
        if result.stderr.strip():
            parts.append(result.stderr.strip())
        if result.stdout.strip():
            parts.append(result.stdout.strip())
        output = "\n".join(parts)
    if len(output) > _MAX_OUTPUT_BYTES:
        output = output[:_MAX_OUTPUT_BYTES] + f"\n[TRUNCATED — output exceeded {_MAX_OUTPUT_BYTES} bytes. Use smaller maxResults or --fields to reduce response size.]"
    return output

# ---------------------------------------------------------------------------
# Background download tools — file content never enters the LLM context
# ---------------------------------------------------------------------------

def _run_job(job_id: str, cmd: str) -> None:
    result = subprocess.run(cmd, shell=True, capture_output=True)
    _jobs[job_id]["stdout"] = result.stdout.decode(errors="replace")
    _jobs[job_id]["stderr"] = result.stderr.decode(errors="replace")
    _jobs[job_id]["returncode"] = result.returncode
    _jobs[job_id]["cmd"] = cmd
    if result.returncode == 0:
        _jobs[job_id]["status"] = "done"
    else:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = result.stderr.decode(errors="replace")

def archive_gmail_attachment(message_id: str, attachment_id: str, filename: str, dest_dir: str = _DEFAULT_ARCHIVE_DIR) -> str:
    """Download a Gmail attachment to local disk in the background.

    File content never passes through the LLM. Call check_archive_job to confirm completion.

    Args:
        message_id: Gmail message ID containing the attachment
        attachment_id: Attachment ID from the message payload parts
        filename: Local filename to save as
        dest_dir: Destination directory (default: ~/archive)
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    # repr() produces a valid Python string literal regardless of path content.
    # shlex.quote() is for shell context only — paths with no special chars come
    # back unquoted and break the python -c source code.
    py_dest = repr(dest_path)
    job_id = uuid.uuid4().hex[:8]
    cmd = (
        f"gws gmail users messages attachments get "
        f'--params \'{{"userId":"me","messageId":"{message_id}","id":"{attachment_id}"}}\' '
        f"| python3 -c \""
        f"import sys,json,base64; d=json.load(sys.stdin); "
        f"open({py_dest},'wb').write(base64.urlsafe_b64decode(d['data']+'=='))"
        f"\""
    )
    _jobs[job_id] = {"status": "running", "dest": dest_path, "error": ""}
    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return f"Job {job_id} started — saving to {dest_path}"

def archive_drive_file(file_id: str, filename: str, dest_dir: str = _DEFAULT_ARCHIVE_DIR) -> str:
    """Download a Google Drive file to local disk in the background.

    File content never passes through the LLM. Call check_archive_job to confirm completion.

    Args:
        file_id: The Drive file ID to download
        filename: Local filename to save as
        dest_dir: Destination directory (default: ~/archive)
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    job_id = uuid.uuid4().hex[:8]
    cmd = (
        f"gws drive files get "
        f'--params \'{{"fileId":"{file_id}","alt":"media"}}\' '
        f"--output {shlex.quote(dest_path)}"
    )
    _jobs[job_id] = {"status": "running", "dest": dest_path, "error": ""}
    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return f"Job {job_id} started — saving to {dest_path}"

def check_archive_job(job_id: str) -> str:
    """Check the status of a background archive download job.

    Args:
        job_id: Job ID returned by archive_gmail_attachment or archive_drive_file
    """
    if job_id not in _jobs:
        return f"Unknown job ID: {job_id}"
    job = _jobs[job_id]
    if job["status"] == "running":
        return f"Job {job_id} is still in progress."
    if job["status"] == "done":
        return f"Job {job_id} complete — file saved to {job['dest']}"
    return (
        f"Job {job_id} failed (exit code {job.get('returncode')}).\n"
        f"Command: {job.get('cmd')}\n"
        f"stderr: {job.get('stderr', '(empty)')}\n"
        f"stdout: {job.get('stdout', '(empty)')}"
    )

# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

docs_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://workspace-developer.goog/mcp",
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=30.0,
        sse_read_timeout=300.0,
    )
)

archive_tools = [archive_gmail_attachment, archive_drive_file, check_archive_job]
workspace_tools = [gws_cli, docs_toolset]

_callbacks = dict(
    before_model_callback=_before_model,
    after_model_callback=_after_model,
    before_tool_callback=_before_tool,
    after_tool_callback=_after_tool,
)

sentinel = LlmAgent(
    model='gemini-3-flash-preview',
    name='storage_sentinel',
    description='Identify large files/attachments, download them, and queue for deletion.',
    instruction=build_skill("sentinel"),
    tools=workspace_tools + archive_tools,
    output_key="sentinel_progress",
    **_callbacks
)

auction = LlmAgent(
    model='gemini-3-flash-preview',
    name='auction_intelligence',
    description='Scrape pricing data from auction emails and save to JSON/CSV.',
    instruction=build_skill("auction"),
    tools=workspace_tools,
    output_key="auction_progress",
    **_callbacks
)

gardener = LlmAgent(
    model='gemini-3-flash-preview',
    name='inbox_gardener',
    description='Mass-unsubscribe and triage emails.',
    instruction=build_skill("gardener"),
    tools=workspace_tools,
    output_key="gardener_progress",
    **_callbacks
)

architect = LlmAgent(
    model='gemini-3.1-pro-preview',
    name='drive_architect',
    description='Audit and reorganize the Google Drive "Root" folder.',
    instruction=build_skill("architect"),
    tools=workspace_tools + archive_tools,
    output_key="architect_progress",
    **_callbacks
)

organizer = LlmAgent(
    model='gemini-3.1-pro-preview',
    name='content_organizer',
    description='Crawl Drive folders and docs, propose a directory taxonomy, tag files with Drive properties, and flag empty or artifact files for deletion.',
    instruction=build_skill("organizer"),
    tools=workspace_tools,
    output_key="organizer_progress",
    **_callbacks
)

_ROOT_INSTRUCTION = """\
You are the orchestrator for a suite of Google Workspace agents.

## Routing
Delegate to the appropriate sub-agent:
- storage_sentinel   — find and archive large Gmail attachments
- auction_intelligence — extract bid data from auction emails
- inbox_gardener     — unsubscribe and triage inbox
- drive_architect    — audit and reorganize Drive root folder
- content_organizer  — crawl all Drive folders, propose taxonomy, tag files, flag artifacts

## Agent Loop — CRITICAL
Every sub-agent ends its response with a JSON progress report:
```json
{"status": "complete|incomplete", "processed": N, "target": M, "next_page_token": "..."|null, "summary": "..."}
```

After each sub-agent returns, read its progress report and apply this logic:

1. If `status` is `"complete"` or `processed >= target`: the task is done. Summarize results to the user.
2. If `status` is `"incomplete"` and `processed < target`: re-delegate to the **same sub-agent** with a continuation message:
   > "Continue. Already processed: <processed>. Resume from page token: <next_page_token>. Target: <target>."
3. If the sub-agent reports an error 3 times in a row on the same step: stop and surface the error to the user with full details. Do NOT loop indefinitely.
4. **Hard cap: stop after 25 re-delegations per user request.** If the task is still incomplete, report what was accomplished so far and tell the user to re-run to continue. This prevents runaway loops.

"""

root_agent = LlmAgent(
    model='gemini-3.1-pro-preview',
    name='root_agent',
    description='Multi-agent suite for Google Workspace management.',
    instruction=_ROOT_INSTRUCTION,
    sub_agents=[sentinel, auction, gardener, architect, organizer]
)
