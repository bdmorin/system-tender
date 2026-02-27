"""Tests for system_tender.cli using click.testing.CliRunner."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from system_tender.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def initialized_config(tmp_path):
    """Initialize a config dir and return the path."""
    from system_tender.config import init_config_dir

    return init_config_dir(tmp_path / "cli-config")


class TestVersion:
    def test_version_flag(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "system-tender" in result.output
        assert "0.1.0" in result.output


class TestHelp:
    def test_help_flag(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "system-tender" in result.output
        assert "Smart cron" in result.output

    def test_run_help(self, runner):
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "maintenance task" in result.output.lower() or "task" in result.output.lower()


class TestInit:
    def test_creates_directory_structure(self, runner, tmp_path):
        target = tmp_path / "init-test"
        result = runner.invoke(main, ["--config-dir", str(target), "init"])
        assert result.exit_code == 0
        assert "Initialized" in result.output
        assert (target / "config.toml").exists()
        assert (target / "tasks").is_dir()
        assert (target / "logs").is_dir()
        assert (target / "runs").is_dir()


class TestList:
    def test_list_with_tasks(self, runner, initialized_config):
        result = runner.invoke(
            main, ["--config-dir", str(initialized_config), "list"]
        )
        assert result.exit_code == 0
        assert "system-check" in result.output

    def test_list_empty(self, runner, tmp_path):
        # Config dir with no tasks
        cfg = tmp_path / "empty-cfg"
        cfg.mkdir()
        (cfg / "tasks").mkdir()
        result = runner.invoke(main, ["--config-dir", str(cfg), "list"])
        assert result.exit_code == 0
        assert "No tasks found" in result.output


class TestHistory:
    def test_no_history(self, runner, tmp_path):
        cfg = tmp_path / "hist-cfg"
        cfg.mkdir()
        result = runner.invoke(main, ["--config-dir", str(cfg), "history"])
        assert result.exit_code == 0
        assert "No run history" in result.output

    def test_no_runs_dir(self, runner, tmp_path):
        cfg = tmp_path / "no-runs"
        cfg.mkdir()
        result = runner.invoke(main, ["--config-dir", str(cfg), "history"])
        assert result.exit_code == 0
        assert "No run history" in result.output
