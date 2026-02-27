"""Tests for system_tender.config."""

from pathlib import Path

import pytest
import tomli_w

from system_tender.config import (
    find_task,
    init_config_dir,
    list_tasks,
    load_global_config,
    load_task_config,
)
from system_tender.models import (
    GlobalConfig,
    OutputFormat,
    TaskConfig,
    ToolName,
)


class TestLoadGlobalConfig:
    def test_defaults_when_no_file(self, tmp_path):
        cfg = load_global_config(tmp_path)
        assert cfg.model == "claude-sonnet-4-6"
        assert cfg.max_tokens == 4096
        assert cfg.config_dir == tmp_path

    def test_loads_from_file(self, tmp_config_dir):
        cfg = load_global_config(tmp_config_dir)
        assert cfg.model == "claude-sonnet-4-6"
        assert cfg.max_tokens == 4096
        assert cfg.default_timeout == 300
        assert cfg.config_dir == tmp_config_dir

    def test_overrides_from_file(self, tmp_path):
        data = {
            "tender": {
                "model": "claude-opus-4-6",
                "max_tokens": 8192,
                "default_timeout": 600,
            }
        }
        config_file = tmp_path / "config.toml"
        with open(config_file, "wb") as f:
            tomli_w.dump(data, f)

        cfg = load_global_config(tmp_path)
        assert cfg.model == "claude-opus-4-6"
        assert cfg.max_tokens == 8192
        assert cfg.default_timeout == 600


class TestLoadTaskConfig:
    def test_basic_task(self, sample_task_toml):
        task = load_task_config(sample_task_toml)
        assert task.name == "disk-check"
        assert task.description == "Check disk usage"
        assert task.timeout == 120
        assert ToolName.SHELL in task.allowed_tools
        assert ToolName.FILE_READ in task.allowed_tools
        assert task.output_format == OutputFormat.TEXT
        assert task.schedule == "0 6 * * *"

    def test_task_with_prompt_object(self, tmp_path):
        data = {
            "task": {
                "name": "fancy",
                "prompt": {
                    "text": "do something fancy",
                    "context_files": ["/etc/hosts"],
                },
            },
        }
        task_file = tmp_path / "fancy.toml"
        with open(task_file, "wb") as f:
            tomli_w.dump(data, f)

        task = load_task_config(task_file)
        assert task.prompt_text == "do something fancy"

    def test_task_with_env(self, tmp_path):
        data = {
            "task": {
                "name": "env-test",
                "prompt": "test",
            },
            "env": {
                "MY_VAR": "hello",
                "OTHER": "world",
            },
        }
        task_file = tmp_path / "env.toml"
        with open(task_file, "wb") as f:
            tomli_w.dump(data, f)

        task = load_task_config(task_file)
        assert task.env == {"MY_VAR": "hello", "OTHER": "world"}

    def test_task_structured_output(self, tmp_path):
        data = {
            "task": {
                "name": "structured",
                "prompt": "report",
            },
            "output": {
                "format": "structured",
            },
        }
        task_file = tmp_path / "structured.toml"
        with open(task_file, "wb") as f:
            tomli_w.dump(data, f)

        task = load_task_config(task_file)
        assert task.output_format == OutputFormat.STRUCTURED


class TestFindTask:
    def test_exact_match(self, sample_task_toml, sample_global_config):
        result = find_task("disk-check", sample_global_config)
        assert result is not None
        assert result.name == "disk-check.toml"

    def test_case_insensitive(self, sample_task_toml, sample_global_config):
        result = find_task("Disk-Check", sample_global_config)
        assert result is not None

    def test_not_found(self, sample_global_config):
        result = find_task("nonexistent", sample_global_config)
        assert result is None

    def test_no_tasks_dir(self, tmp_path):
        cfg = GlobalConfig(config_dir=tmp_path)
        result = find_task("anything", cfg)
        assert result is None


class TestListTasks:
    def test_empty(self, tmp_path):
        (tmp_path / "tasks").mkdir()
        cfg = GlobalConfig(config_dir=tmp_path)
        assert list_tasks(cfg) == []

    def test_no_tasks_dir(self, tmp_path):
        cfg = GlobalConfig(config_dir=tmp_path)
        assert list_tasks(cfg) == []

    def test_finds_tasks(self, sample_task_toml, sample_global_config):
        tasks = list_tasks(sample_global_config)
        assert len(tasks) == 1
        assert tasks[0].name == "disk-check"

    def test_skips_bad_files(self, tmp_config_dir, sample_global_config):
        # Write an invalid TOML file
        bad_file = tmp_config_dir / "tasks" / "bad.toml"
        bad_file.write_text("this is not valid toml {{{{")
        tasks = list_tasks(sample_global_config)
        # Should skip the bad file without crashing
        assert isinstance(tasks, list)


class TestInitConfigDir:
    def test_creates_structure(self, tmp_path):
        target = tmp_path / "new-config"
        result = init_config_dir(target)
        assert result == target
        assert (target / "tasks").is_dir()
        assert (target / "logs").is_dir()
        assert (target / "runs").is_dir()
        assert (target / "config.toml").is_file()

    def test_creates_example_task(self, tmp_path):
        target = tmp_path / "fresh"
        init_config_dir(target)
        task_files = list((target / "tasks").glob("*.toml"))
        assert len(task_files) == 1
        assert task_files[0].name == "system-check.toml"

    def test_idempotent(self, tmp_path):
        target = tmp_path / "idem"
        init_config_dir(target)
        init_config_dir(target)  # second call should not fail
        assert (target / "config.toml").is_file()

    def test_does_not_overwrite_config(self, tmp_path):
        target = tmp_path / "keep"
        init_config_dir(target)
        # Write custom content
        (target / "config.toml").write_text("# custom")
        init_config_dir(target)
        assert (target / "config.toml").read_text() == "# custom"
