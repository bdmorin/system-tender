"""Configuration loading and management for system-tender."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from .models import GlobalConfig, OutputFormat, TaskConfig, TaskPrompt, ToolName

DEFAULT_CONFIG_DIR = Path("~/.config/system-tender").expanduser()


def load_global_config(config_dir: Path | None = None) -> GlobalConfig:
    """Load global configuration from config.toml."""
    config_dir = config_dir or DEFAULT_CONFIG_DIR
    config_file = config_dir / "config.toml"

    if not config_file.exists():
        return GlobalConfig(config_dir=config_dir)

    with open(config_file, "rb") as f:
        data = tomllib.load(f)

    # Flatten nested sections
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value

    flat["config_dir"] = config_dir
    return GlobalConfig(**flat)


def load_task_config(path: Path) -> TaskConfig:
    """Load a task configuration from a TOML file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    task_data = data.get("task", data)

    # Handle prompt as string or dict
    prompt_raw = task_data.get("prompt", "")
    if isinstance(prompt_raw, dict):
        task_data["prompt"] = TaskPrompt(**prompt_raw)

    # Handle allowed_tools as strings
    if "allowed_tools" in task_data:
        task_data["allowed_tools"] = [
            ToolName(t) for t in task_data["allowed_tools"]
        ]

    # Handle output section
    output_data = data.get("output", {})
    if "format" in output_data:
        task_data["output_format"] = OutputFormat(output_data["format"])

    # Handle schedule
    schedule_data = data.get("schedule", {})
    if "cron" in schedule_data:
        task_data["schedule"] = schedule_data["cron"]

    # Handle env
    env_data = data.get("env", {})
    if env_data:
        task_data["env"] = env_data

    return TaskConfig(**task_data)


def find_task(name: str, config: GlobalConfig) -> Path | None:
    """Find a task file by name in the tasks directory."""
    tasks_dir = config.tasks_dir
    if not tasks_dir.exists():
        return None

    # Try exact match first
    exact = tasks_dir / f"{name}.toml"
    if exact.exists():
        return exact

    # Try case-insensitive
    for f in tasks_dir.iterdir():
        if f.suffix == ".toml" and f.stem.lower() == name.lower():
            return f

    return None


def list_tasks(config: GlobalConfig) -> list[TaskConfig]:
    """List all configured tasks."""
    tasks_dir = config.tasks_dir
    if not tasks_dir.exists():
        return []

    configs = []
    for f in sorted(tasks_dir.iterdir()):
        if f.suffix == ".toml":
            try:
                configs.append(load_task_config(f))
            except Exception:
                continue
    return configs


def init_config_dir(config_dir: Path | None = None) -> Path:
    """Initialize the config directory with example files."""
    config_dir = config_dir or DEFAULT_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)
    (config_dir / "tasks").mkdir(exist_ok=True)
    (config_dir / "logs").mkdir(exist_ok=True)
    (config_dir / "runs").mkdir(exist_ok=True)

    # Write default config if not exists
    config_file = config_dir / "config.toml"
    if not config_file.exists():
        default = {
            "tender": {
                "model": "claude-sonnet-4-6",
                "max_tokens": 4096,
                "default_timeout": 300,
            }
        }
        with open(config_file, "wb") as f:
            tomli_w.dump(default, f)

    # Write example task if tasks dir is empty
    tasks_dir = config_dir / "tasks"
    if not list(tasks_dir.glob("*.toml")):
        example = {
            "task": {
                "name": "system-check",
                "description": "Quick system health check",
                "prompt": "Check disk usage, memory, and load average. Report anything concerning.",
                "allowed_tools": ["shell", "file_read"],
                "timeout": 120,
            },
            "output": {
                "format": "text",
            },
        }
        with open(tasks_dir / "system-check.toml", "wb") as f:
            tomli_w.dump(example, f)

    return config_dir
