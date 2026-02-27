# system-tender Design Document

**Date**: 2026-02-27
**Status**: Draft

## Overview

system-tender is a smart cron system powered by Anthropic Claude for system maintenance tasks. It replaces static maintenance scripts with an AI-driven agent that can reason about system state, make decisions, and report structured results.

## Architecture

### Invocation Model

`tender` is a CLI tool invoked by any scheduler -- launchd, systemd, cron, or manually from a terminal. system-tender is invocation-agnostic; it does not manage its own scheduling daemon.

### Why Anthropic Client SDK (not Agent SDK)

The Agent SDK wraps Claude Code, which brings a full IDE-oriented agentic environment. That's overkill for system maintenance tasks. The Client SDK gives us:

- Full control over the tool loop
- Explicit safety boundaries per task
- Structured output parsing without framework overhead
- Minimal dependencies on the target system
- Direct management of the message/tool-use cycle

### Configuration

Config root: `~/.config/system-tender/` (XDG-compliant)

```
~/.config/system-tender/
  config.toml          # Global settings (default model, log level, API key ref)
  tasks/
    brew-update.toml
    disk-cleanup.toml
    security-audit.toml
```

## Task Configuration Format

Tasks are TOML files in the `tasks/` subdirectory.

```toml
[task]
name = "brew-update"
description = "Update homebrew and report changes"
system_prompt = "You are a system maintenance agent..."  # optional override
model = "claude-sonnet-4-6"  # optional, defaults to global
timeout = 300
allowed_tools = ["shell", "file_read", "file_write"]

[task.prompt]
text = "Update all homebrew packages. Report what was upgraded, any failures, and whether a restart is needed."

[output]
format = "structured"  # or "text"
```

Fields:

- `name`: Unique identifier for the task.
- `description`: Human-readable summary, also fed to Claude as context.
- `system_prompt`: Optional override for the default system prompt.
- `model`: Model to use. Falls back to global config default.
- `timeout`: Max wall-clock seconds for the entire task run.
- `allowed_tools`: Whitelist of tools Claude can invoke for this task.
- `output.format`: `structured` returns JSON via Claude's structured output. `text` returns the final assistant message as-is.

## Tool Definitions

Four built-in tools, each implemented as a Python function that maps to a Claude tool_use schema:

### shell_execute

Run shell commands via subprocess. Captures stdout, stderr, and exit code. Per-command timeout enforced.

```python
{
    "name": "shell_execute",
    "description": "Execute a shell command and return its output",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30}
        },
        "required": ["command"]
    }
}
```

### file_read

Read file contents. Returns text content or error if file doesn't exist or isn't readable.

```python
{
    "name": "file_read",
    "description": "Read the contents of a file",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the file"}
        },
        "required": ["path"]
    }
}
```

### file_write

Write or append to files. Creates parent directories if needed.

```python
{
    "name": "file_write",
    "description": "Write content to a file",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the file"},
            "content": {"type": "string", "description": "Content to write"},
            "mode": {"type": "string", "enum": ["write", "append"], "default": "write"}
        },
        "required": ["path", "content"]
    }
}
```

### http_request

Make HTTP requests for webhook callbacks, health checks, or API calls.

```python
{
    "name": "http_request",
    "description": "Make an HTTP request",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to request"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"], "default": "GET"},
            "headers": {"type": "object", "description": "HTTP headers"},
            "body": {"type": "string", "description": "Request body"}
        },
        "required": ["url"]
    }
}
```

## Safety Model

### Tool Opt-In

Tools are opt-in per task via the `allowed_tools` field. A task that only needs to read files cannot execute shell commands, even if Claude requests it. If Claude requests a tool not in the allowed list, the tool call is rejected and an error is returned in the tool result.

### Execution Boundaries

- Shell commands have configurable per-command timeout (default 30s) and per-task timeout.
- No tool executes without Claude explicitly requesting it through the API tool_use mechanism.
- All tool calls are logged with full input/output before execution proceeds.

### No Implicit Escalation

system-tender runs with the permissions of the invoking user. It does not sudo, does not modify its own config, and does not install packages outside of what a task explicitly requests.

## Logging Strategy

### Platform-Aware Logging

| Platform | Primary Backend | Fallback |
|----------|----------------|----------|
| macOS    | syslog (os.log) | File    |
| Linux    | journald (systemd) | File |
| Any      | File (~/.config/system-tender/logs/) | Always available |

### Structured Log Fields

Every log entry includes:

- `task_name`: Which task produced this entry
- `run_id`: UUID for the specific invocation
- `timestamp`: ISO 8601
- `level`: debug, info, warn, error
- `tool_calls`: List of tools invoked (for run summaries)
- `token_usage`: Input/output token counts

### Console Output

Interactive use gets Rich-formatted console output. When running under a scheduler (detected via TTY check), output is plain text suitable for log capture.

## Scheduler Integration

system-tender does not manage schedules itself. Instead, `tender generate-schedule` produces native scheduler configs.

### Supported Targets

- **launchd**: Generates a `.plist` file for macOS LaunchAgent/LaunchDaemon
- **systemd**: Generates a `.timer` + `.service` unit pair
- **cron**: Generates a crontab entry

### Per-Task Scheduling

Each task TOML can include an optional schedule block:

```toml
[schedule]
interval = "daily"       # or "hourly", "weekly", cron expression
time = "03:00"           # for daily/weekly
day = "sunday"           # for weekly
```

`tender generate-schedule --target launchd` reads all tasks with schedule blocks and produces the corresponding plist files.

## The Agentic Loop

```
1. Load task config from TOML
2. Build system prompt (global default + task override)
3. Build tool definitions (filtered by allowed_tools)
4. Send initial message to Claude API with tools
5. Receive response
   a. If stop_reason == "tool_use":
      - Validate tool is in allowed_tools
      - Execute tool, capture result
      - Append tool_result to messages
      - Go to step 4
   b. If stop_reason == "end_turn":
      - Capture final response
6. Parse structured output if format == "structured"
7. Log run summary (token usage, tool calls, duration)
8. Save to run history (~/.config/system-tender/history/)
```

### Error Handling

- API errors: Retry with exponential backoff (3 attempts max), then fail the task.
- Tool execution errors: Return error text as tool_result so Claude can reason about the failure.
- Timeout: Kill the task, log the timeout, return non-zero exit code.

## Project Structure

```
system-tender/
  src/system_tender/
    __init__.py
    cli.py              # Click/Typer CLI entry point
    config.py           # Config loading and validation
    tools/
      __init__.py
      shell.py           # shell_execute implementation
      files.py           # file_read, file_write
      http.py            # http_request
    loop.py              # Agentic loop (Claude API interaction)
    logging.py           # Platform-aware logging setup
    scheduler.py         # Schedule generation (launchd, systemd, cron)
    models.py            # Pydantic models for config, output, run history
  tests/
  docs/
  pyproject.toml
```

## Future Considerations

- **Task chaining**: Output of one task feeds as input to another. DAG-based execution.
- **Webhook triggers**: Lightweight HTTP endpoint that accepts events and maps them to tasks.
- **Approval gates**: Pause before destructive actions, notify via webhook/email, wait for human approval.
- **Cost tracking**: Per-task token usage aggregation and budget limits.
- **Secret management**: Integration with system keychain or external secret stores for API keys and credentials referenced in tasks.
