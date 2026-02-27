"""Core agentic engine for system-tender.

Runs the Anthropic client SDK tool-use loop with configurable tools.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import platform
import shlex
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from .models import (
    GlobalConfig,
    TaskConfig,
    TaskResult,
    ToolCall,
    ToolName,
)

logger = logging.getLogger("system-tender")

# Only env vars matching these prefixes are loaded from the .env file.
# Prevents hijacking PATH, LD_PRELOAD, PYTHONPATH, etc.
_ENV_ALLOWED_PREFIXES = ("ANTHROPIC_",)


def _load_env(config_dir: Path) -> None:
    """Load .env file from config dir if ANTHROPIC_API_KEY not already set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env_file = config_dir / ".env"
    if not env_file.exists():
        return

    # Warn if .env is readable by group/others
    file_mode = env_file.stat().st_mode
    if file_mode & 0o077:
        logger.warning(
            ".env file %s has overly permissive permissions (%o). "
            "Recommend: chmod 600 %s",
            env_file, file_mode & 0o777, env_file,
        )

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if not key or not value:
                continue
            if any(key.startswith(p) for p in _ENV_ALLOWED_PREFIXES):
                os.environ[key] = value
            else:
                logger.warning(
                    "Ignoring non-allowlisted env var in .env: %s",
                    key,
                )
    logger.debug("Loaded environment from %s", env_file)


# --- Tool Definitions (Anthropic API format) ---

TOOL_DEFINITIONS: dict[ToolName, dict[str, Any]] = {
    ToolName.SHELL: {
        "name": "shell_execute",
        "description": (
            "Execute a shell command and return stdout/stderr. "
            "Commands run in a subprocess with a timeout. "
            "Use for system administration: package updates, service management, "
            "disk operations, process inspection, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 60)",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory for the command (optional)",
                },
            },
            "required": ["command"],
        },
    },
    ToolName.FILE_READ: {
        "name": "file_read",
        "description": (
            "Read the contents of a file. Returns the full text content. "
            "Use for reading config files, logs, reports, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to read (default: 1048576 / 1MB)",
                },
            },
            "required": ["path"],
        },
    },
    ToolName.FILE_WRITE: {
        "name": "file_write",
        "description": (
            "Write content to a file. Creates parent directories if needed. "
            "Use for saving reports, updating configs, writing scripts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
                "append": {
                    "type": "boolean",
                    "description": "Append instead of overwrite (default: false)",
                },
            },
            "required": ["path", "content"],
        },
    },
    ToolName.HTTP_REQUEST: {
        "name": "http_request",
        "description": (
            "Make an HTTP request. Returns status code, headers, and body. "
            "Use for webhook callbacks, health checks, API calls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to request",
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method (default: GET)",
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                },
                "headers": {
                    "type": "object",
                    "description": "HTTP headers as key-value pairs",
                    "additionalProperties": {"type": "string"},
                },
                "body": {
                    "type": "string",
                    "description": "Request body (for POST/PUT/PATCH)",
                },
            },
            "required": ["url"],
        },
    },
    ToolName.NOTIFY: {
        "name": "notify",
        "description": (
            "Send a native system notification. "
            "On macOS uses Notification Center, on Linux uses notify-send. "
            "Use for alerting the user about task results, warnings, or completions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Notification title",
                },
                "message": {
                    "type": "string",
                    "description": "Notification body text",
                },
                "sound": {
                    "type": "boolean",
                    "description": "Play a sound with the notification (default: true, macOS only)",
                },
            },
            "required": ["title", "message"],
        },
    },
}

# Map API tool names back to our ToolName enum
_TOOL_NAME_MAP = {defn["name"]: tool_name for tool_name, defn in TOOL_DEFINITIONS.items()}


# --- Tool Execution ---

MAX_SHELL_TIMEOUT = 3600  # Hard cap: 1 hour


def execute_shell(command: str, timeout: int = 60, working_dir: str | None = None) -> str:
    """Execute a shell command and return output."""
    timeout = max(1, min(timeout, MAX_SHELL_TIMEOUT))
    logger.info("shell: %s", command)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
        )
        output_parts = []
        if result.stdout:
            output_parts.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            output_parts.append(f"stderr:\n{result.stderr}")
        output_parts.append(f"exit_code: {result.returncode}")
        return "\n".join(output_parts)
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {timeout} seconds"
    except Exception as e:
        return f"ERROR: {e}"


def execute_file_read(path: str, max_bytes: int = 1_048_576) -> str:
    """Read a file and return its contents."""
    logger.info("file_read: %s", path)
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return f"ERROR: File not found: {path}"
        if not p.is_file():
            return f"ERROR: Not a file: {path}"
        content = p.read_text(errors="replace")
        if len(content) > max_bytes:
            content = content[:max_bytes] + f"\n... (truncated at {max_bytes} bytes)"
        return content
    except Exception as e:
        return f"ERROR: {e}"


def execute_file_write(path: str, content: str, append: bool = False) -> str:
    """Write content to a file."""
    logger.info("file_write: %s (append=%s)", path, append)
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(p, mode) as f:
            f.write(content)
        return f"OK: Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"ERROR: {e}"


def execute_http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> str:
    """Make an HTTP request and return the response."""
    logger.info("http_request: %s %s", method, url)
    try:
        req = urllib.request.Request(url, method=method)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        if body:
            req.data = body.encode("utf-8")

        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            return (
                f"status: {resp.status}\n"
                f"headers: {dict(resp.headers)}\n"
                f"body:\n{resp_body[:10000]}"
            )
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:2000]
        return f"HTTP {e.code}: {e.reason}\nbody:\n{body_text}"
    except Exception as e:
        return f"ERROR: {e}"


def execute_notify(title: str, message: str, sound: bool = True) -> str:
    """Send a native system notification."""
    logger.info("notify: %s — %s", title, message)
    system = platform.system()

    try:
        if system == "Darwin":
            # Escape backslashes first, then double quotes for osascript
            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{safe_message}" with title "{safe_title}"'
            if sound:
                script += ' sound name "default"'
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return f"OK: Notification sent — {title}"

        elif system == "Linux":
            if not shutil.which("notify-send"):
                return "ERROR: notify-send not found. Install libnotify-bin."
            subprocess.run(
                ["notify-send", title, message],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return f"OK: Notification sent — {title}"

        else:
            return f"ERROR: Notifications not supported on {system}"

    except subprocess.TimeoutExpired:
        return "ERROR: Notification command timed out"
    except Exception as e:
        return f"ERROR: {e}"


# --- Sensitive Data Redaction ---

_SENSITIVE_HEADER_KEYS = frozenset({
    "authorization", "x-api-key", "cookie", "set-cookie",
    "proxy-authorization", "x-auth-token",
})


def _redact_tool_input(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of tool_input with sensitive values redacted for storage."""
    redacted = dict(tool_input)
    if tool_name == "http_request" and isinstance(redacted.get("headers"), dict):
        redacted["headers"] = {
            k: "[REDACTED]" if k.lower() in _SENSITIVE_HEADER_KEYS else v
            for k, v in redacted["headers"].items()
        }
    return redacted


# --- Network Access Control ---

def check_egress_allowed(url: str, task: TaskConfig | None) -> str | None:
    """Check if an HTTP request is allowed by the task's network policy.

    Returns None if allowed, or an error message string if denied.

    NOTE: This only restricts the http_request tool. Shell commands like
    `curl` or `brew update` still have full network access because they
    run as subprocesses. True network isolation would require OS-level
    sandboxing (e.g., macOS sandbox-exec or Linux namespaces).
    """
    if task is None:
        return None

    if not task.network_access:
        return (
            "ERROR: Network access denied. "
            "Set network_access = true in task config to allow HTTP requests."
        )

    if not task.egress_allowlist:
        return None

    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""

    for pattern in task.egress_allowlist:
        if pattern == "*":
            return None
        if fnmatch.fnmatch(hostname, pattern):
            return None

    allowed = ", ".join(task.egress_allowlist)
    return (
        f"ERROR: Egress denied for host '{hostname}'. "
        f"Allowed hosts: [{allowed}]"
    )


# --- Tool Dispatcher ---

def dispatch_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    task: TaskConfig | None = None,
) -> tuple[str, bool]:
    """Dispatch a tool call and return (output, success).

    The optional `task` parameter enables network access control for
    http_request calls. When provided, the task's network_access and
    egress_allowlist fields are checked before executing HTTP requests.
    """
    try:
        if tool_name == "shell_execute":
            result = execute_shell(
                command=tool_input["command"],
                timeout=tool_input.get("timeout", 60),
                working_dir=tool_input.get("working_dir"),
            )
            success = "ERROR:" not in result
            return result, success

        elif tool_name == "file_read":
            result = execute_file_read(
                path=tool_input["path"],
                max_bytes=tool_input.get("max_bytes", 1_048_576),
            )
            success = not result.startswith("ERROR:")
            return result, success

        elif tool_name == "file_write":
            result = execute_file_write(
                path=tool_input["path"],
                content=tool_input["content"],
                append=tool_input.get("append", False),
            )
            success = result.startswith("OK:")
            return result, success

        elif tool_name == "http_request":
            egress_error = check_egress_allowed(tool_input.get("url", ""), task)
            if egress_error:
                return egress_error, False
            result = execute_http_request(
                url=tool_input["url"],
                method=tool_input.get("method", "GET"),
                headers=tool_input.get("headers"),
                body=tool_input.get("body"),
            )
            success = not result.startswith("ERROR:")
            return result, success

        elif tool_name == "notify":
            result = execute_notify(
                title=tool_input["title"],
                message=tool_input["message"],
                sound=tool_input.get("sound", True),
            )
            success = result.startswith("OK:")
            return result, success

        else:
            return f"ERROR: Unknown tool: {tool_name}", False

    except Exception as e:
        return f"ERROR: Tool execution failed: {e}", False


# --- The Agentic Loop ---

def build_system_prompt(task: TaskConfig, global_config: GlobalConfig) -> str:
    """Build the system prompt for a task."""
    base = task.system_prompt or global_config.default_system_prompt

    tool_names = ", ".join(t.value for t in task.allowed_tools)
    return (
        f"{base}\n\n"
        f"Available tools: {tool_names}\n"
        f"Task timeout: {task.timeout}s\n"
        f"Report your results clearly and concisely when done."
    )


def build_tool_list(task: TaskConfig) -> list[dict[str, Any]]:
    """Build the tool definitions list for the API call."""
    return [TOOL_DEFINITIONS[t] for t in task.allowed_tools if t in TOOL_DEFINITIONS]


def run_task(
    task: TaskConfig,
    global_config: GlobalConfig,
    prompt_override: str | None = None,
) -> TaskResult:
    """Execute a task through the agentic loop.

    This is the core of system-tender. It:
    1. Builds the system prompt and tool list
    2. Sends the prompt to Claude
    3. Handles tool_use responses by executing tools and looping
    4. Captures the final text response
    5. Returns a structured TaskResult
    """
    run_start = time.monotonic()
    started_at = datetime.now(timezone.utc)
    model = task.model or global_config.model
    prompt_text = prompt_override or task.prompt_text

    result = TaskResult(
        task_name=task.name,
        started_at=started_at,
        model=model,
    )

    logger.info("Starting task: %s (run_id=%s, model=%s)", task.name, result.run_id, model)

    try:
        # Load .env from config dir if API key not already in env
        _load_env(global_config.config_dir)
        client = anthropic.Anthropic()
        system_prompt = build_system_prompt(task, global_config)
        tools = build_tool_list(task)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt_text}]

        total_input_tokens = 0
        total_output_tokens = 0
        max_iterations = 25  # safety valve

        for iteration in range(max_iterations):
            logger.debug("API call iteration %d", iteration + 1)

            response = client.messages.create(
                model=model,
                max_tokens=global_config.max_tokens,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # Check for tool use
            if response.stop_reason == "tool_use":
                # Process all tool calls in the response
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_start = time.monotonic()

                        # Check if tool is allowed
                        api_tool_name = block.name
                        our_tool = _TOOL_NAME_MAP.get(api_tool_name)
                        if our_tool and our_tool not in task.allowed_tools:
                            output = f"ERROR: Tool '{api_tool_name}' is not allowed for this task"
                            success = False
                        else:
                            output, success = dispatch_tool(api_tool_name, block.input, task=task)

                        tool_duration = int((time.monotonic() - tool_start) * 1000)

                        result.tool_calls.append(ToolCall(
                            tool_name=api_tool_name,
                            input=_redact_tool_input(api_tool_name, block.input),
                            output=output[:2000],  # truncate for storage
                            duration_ms=tool_duration,
                            success=success,
                        ))

                        logger.info(
                            "Tool %s: %s (%dms)",
                            "OK" if success else "FAIL",
                            api_tool_name,
                            tool_duration,
                        )

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                        })

                # Send tool results back to Claude
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            else:
                # end_turn — extract final text
                final_text_parts = []
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text_parts.append(block.text)

                result.output = "\n".join(final_text_parts)
                result.success = True
                logger.info("Task completed successfully")
                break

        else:
            # Hit max iterations
            result.error = f"Hit maximum iteration limit ({max_iterations})"
            result.success = False
            logger.warning("Task hit max iterations: %d", max_iterations)

    except anthropic.APIError as e:
        result.error = f"API error: {e.message}"
        result.success = False
        logger.error("API error: %s", e.message)

    except Exception as e:
        result.error = f"Unexpected error: {e}"
        result.success = False
        logger.exception("Unexpected error during task execution")

    # Finalize
    result.completed_at = datetime.now(timezone.utc)
    result.input_tokens = total_input_tokens
    result.output_tokens = total_output_tokens
    result.duration_ms = int((time.monotonic() - run_start) * 1000)

    logger.info(
        "Task %s: %s in %dms (%d tokens)",
        task.name,
        "OK" if result.success else "FAILED",
        result.duration_ms,
        total_input_tokens + total_output_tokens,
    )

    return result


def save_run(result: TaskResult, config: GlobalConfig) -> Path:
    """Save a run result to the runs directory."""
    runs_dir = config.runs_dir
    runs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = result.started_at.strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{result.task_name}-{result.run_id}.json"
    path = runs_dir / filename

    with open(path, "w") as f:
        f.write(result.model_dump_json(indent=2))
    os.chmod(path, 0o600)

    logger.debug("Saved run to %s", path)
    return path
