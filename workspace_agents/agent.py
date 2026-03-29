import json
import os
import shlex
import subprocess
import threading
import uuid
from pathlib import Path
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

def _skill(name: str) -> str:
    return (Path(__file__).parent / "skills" / f"{name}.md").read_text()

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
    output = result.stdout if result.returncode == 0 else f"Error: {result.stderr}"
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
    quoted_dest = shlex.quote(dest_path)
    job_id = uuid.uuid4().hex[:8]
    cmd = (
        f"gws gmail users messages attachments get "
        f'--params \'{{"userId":"me","messageId":"{message_id}","id":"{attachment_id}"}}\' '
        f"| python3 -c \""
        f"import sys,json,base64; d=json.load(sys.stdin); "
        f"open({quoted_dest!r},'wb').write(base64.urlsafe_b64decode(d['data']+'=='))"
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

sentinel = LlmAgent(
    model='gemini-3-flash-preview',
    name='storage_sentinel',
    description='Identify large files/attachments, download them, and queue for deletion.',
    instruction=_skill("sentinel"),
    tools=workspace_tools + archive_tools
)

auction = LlmAgent(
    model='gemini-3-flash-preview',
    name='auction_intelligence',
    description='Scrape pricing data from auction emails and save to JSON/CSV.',
    instruction=_skill("auction"),
    tools=workspace_tools
)

gardener = LlmAgent(
    model='gemini-3-flash-preview',
    name='inbox_gardener',
    description='Mass-unsubscribe and triage emails.',
    instruction=_skill("gardener"),
    tools=workspace_tools
)

architect = LlmAgent(
    model='gemini-3.1-pro-preview',
    name='drive_architect',
    description='Audit and reorganize the Google Drive "Root" folder.',
    instruction=_skill("architect"),
    tools=workspace_tools + archive_tools
)

root_agent = LlmAgent(
    model='gemini-3.1-pro-preview',
    name='root_agent',
    description='Multi-agent suite for Google Workspace management.',
    instruction='You are the orchestrator for a suite of Google Workspace agents. Delegate tasks to the Storage Sentinel, Auction Intelligence, Inbox Gardener, or Drive Architect based on the user request.',
    sub_agents=[sentinel, auction, gardener, architect]
)
