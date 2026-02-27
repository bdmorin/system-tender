"""Data models for system-tender."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ToolName(str, Enum):
    """Available tools for task execution."""
    SHELL = "shell"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    HTTP_REQUEST = "http_request"


class OutputFormat(str, Enum):
    """Output format for task results."""
    TEXT = "text"
    STRUCTURED = "structured"


class TaskPrompt(BaseModel):
    """The prompt configuration for a task."""
    text: str
    context_files: list[str] = Field(default_factory=list)


class TaskConfig(BaseModel):
    """Configuration for a single maintenance task."""
    name: str
    description: str = ""
    system_prompt: str | None = None
    model: str | None = None
    timeout: int = 300
    allowed_tools: list[ToolName] = Field(
        default_factory=lambda: [ToolName.SHELL, ToolName.FILE_READ]
    )
    prompt: TaskPrompt | str = ""
    output_format: OutputFormat = OutputFormat.TEXT
    schedule: str | None = None  # cron-style: "0 6 * * *"
    env: dict[str, str] = Field(default_factory=dict)

    @property
    def prompt_text(self) -> str:
        if isinstance(self.prompt, TaskPrompt):
            return self.prompt.text
        return self.prompt


class GlobalConfig(BaseModel):
    """Global system-tender configuration."""
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 4096
    api_key_env: str = "ANTHROPIC_API_KEY"
    config_dir: Path = Path("~/.config/system-tender").expanduser()
    log_dir: Path | None = None
    default_timeout: int = 300
    default_system_prompt: str = (
        "You are a system maintenance agent. Your job is to execute "
        "system administration tasks reliably and report results clearly. "
        "Be concise. Report what you did, what succeeded, what failed, "
        "and any warnings. Do not ask questions — act on what you're given."
    )

    @property
    def tasks_dir(self) -> Path:
        return self.config_dir / "tasks"

    @property
    def runs_dir(self) -> Path:
        return self.config_dir / "runs"

    @property
    def effective_log_dir(self) -> Path:
        return self.log_dir or (self.config_dir / "logs")


class ToolCall(BaseModel):
    """Record of a single tool invocation."""
    tool_name: str
    input: dict[str, Any]
    output: str
    duration_ms: int = 0
    success: bool = True


class TaskResult(BaseModel):
    """Result of executing a task."""
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    task_name: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    success: bool = False
    output: str = ""
    error: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    duration_ms: int = 0

    def to_summary(self) -> str:
        """One-line status summary (no output content — that's printed separately)."""
        status = "OK" if self.success else "FAILED"
        duration = f"{self.duration_ms / 1000:.1f}s"
        tokens = f"{self.input_tokens + self.output_tokens} tokens"
        tools = f"{len(self.tool_calls)} tool calls"
        lines = [
            f"[{status}] {self.task_name} ({self.run_id})",
            f"  Duration: {duration} | {tokens} | {tools}",
        ]
        if self.error:
            lines.append(f"  Error: {self.error}")
        return "\n".join(lines)
