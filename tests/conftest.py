"""Shared test fixtures for system-tender."""

from pathlib import Path

import pytest
import tomli_w

from system_tender.models import (
    GlobalConfig,
    OutputFormat,
    TaskConfig,
    ToolName,
)


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory with proper structure."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "tasks").mkdir()
    (config_dir / "logs").mkdir()
    (config_dir / "runs").mkdir()

    # Write a minimal config.toml
    config_data = {
        "tender": {
            "model": "claude-sonnet-4-6",
            "max_tokens": 4096,
            "default_timeout": 300,
        }
    }
    with open(config_dir / "config.toml", "wb") as f:
        tomli_w.dump(config_data, f)

    return config_dir


@pytest.fixture
def sample_task_toml(tmp_config_dir: Path) -> Path:
    """Write a sample task TOML file and return its path."""
    task_data = {
        "task": {
            "name": "disk-check",
            "description": "Check disk usage",
            "allowed_tools": ["shell", "file_read"],
            "timeout": 120,
            "prompt": "Check disk usage",
        },
        "output": {
            "format": "text",
        },
        "schedule": {
            "cron": "0 6 * * *",
        },
    }
    task_path = tmp_config_dir / "tasks" / "disk-check.toml"
    with open(task_path, "wb") as f:
        tomli_w.dump(task_data, f)
    return task_path


@pytest.fixture
def sample_task_config() -> TaskConfig:
    """Return a TaskConfig instance with sensible defaults."""
    return TaskConfig(
        name="test-task",
        description="A test task",
        prompt="Run a quick test",
        allowed_tools=[ToolName.SHELL, ToolName.FILE_READ],
        timeout=60,
        output_format=OutputFormat.TEXT,
    )


@pytest.fixture
def sample_global_config(tmp_config_dir: Path) -> GlobalConfig:
    """Return a GlobalConfig pointing at the temporary directory."""
    return GlobalConfig(config_dir=tmp_config_dir)
