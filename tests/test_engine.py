"""Tests for system_tender.engine (tool execution, NOT API calls)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from system_tender.engine import (
    TOOL_DEFINITIONS,
    build_system_prompt,
    build_tool_list,
    dispatch_tool,
    execute_file_read,
    execute_file_write,
    execute_http_request,
    execute_shell,
)
from system_tender.models import (
    GlobalConfig,
    TaskConfig,
    ToolName,
)


class TestExecuteShell:
    def test_basic_command(self):
        result = execute_shell("echo hello")
        assert "hello" in result
        assert "exit_code: 0" in result

    def test_captures_stderr(self):
        result = execute_shell("echo oops >&2")
        assert "stderr:" in result
        assert "oops" in result

    def test_nonzero_exit_code(self):
        result = execute_shell("exit 42")
        assert "exit_code: 42" in result

    def test_timeout(self):
        result = execute_shell("sleep 10", timeout=1)
        assert "timed out" in result

    def test_working_dir(self, tmp_path):
        result = execute_shell("pwd", working_dir=str(tmp_path))
        assert str(tmp_path) in result

    def test_invalid_working_dir(self):
        result = execute_shell("pwd", working_dir="/nonexistent/path/xyz")
        assert "ERROR:" in result


class TestExecuteFileRead:
    def test_read_existing_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("world")
        result = execute_file_read(str(f))
        assert result == "world"

    def test_read_missing_file(self):
        result = execute_file_read("/nonexistent/file.txt")
        assert "ERROR:" in result
        assert "not found" in result.lower()

    def test_read_directory(self, tmp_path):
        result = execute_file_read(str(tmp_path))
        assert "ERROR:" in result
        assert "Not a file" in result

    def test_truncation(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("A" * 500)
        result = execute_file_read(str(f), max_bytes=100)
        assert "truncated" in result
        assert len(result) < 500


class TestExecuteFileWrite:
    def test_write_new_file(self, tmp_path):
        target = tmp_path / "out.txt"
        result = execute_file_write(str(target), "hello world")
        assert "OK:" in result
        assert target.read_text() == "hello world"

    def test_overwrite(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old")
        execute_file_write(str(target), "new")
        assert target.read_text() == "new"

    def test_append(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("first")
        execute_file_write(str(target), " second", append=True)
        assert target.read_text() == "first second"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "file.txt"
        result = execute_file_write(str(target), "deep content")
        assert "OK:" in result
        assert target.read_text() == "deep content"


class TestExecuteHttpRequest:
    @patch("system_tender.engine.urllib.request.urlopen")
    def test_get_request(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.read.return_value = b"response body"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_http_request("https://example.com")
        assert "status: 200" in result
        assert "response body" in result

    @patch("system_tender.engine.urllib.request.urlopen")
    def test_post_with_body(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.headers = {}
        mock_resp.read.return_value = b"created"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_http_request(
            "https://example.com/api",
            method="POST",
            headers={"Content-Type": "application/json"},
            body='{"key": "value"}',
        )
        assert "status: 201" in result

    @patch("system_tender.engine.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = ConnectionError("refused")
        result = execute_http_request("https://unreachable.local")
        assert "ERROR:" in result


class TestDispatchTool:
    def test_shell_execute(self):
        output, success = dispatch_tool("shell_execute", {"command": "echo hi"})
        assert success is True
        assert "hi" in output

    def test_file_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content")
        output, success = dispatch_tool("file_read", {"path": str(f)})
        assert success is True
        assert output == "content"

    def test_file_read_missing(self):
        output, success = dispatch_tool("file_read", {"path": "/no/such/file"})
        assert success is False
        assert "ERROR:" in output

    def test_file_write(self, tmp_path):
        target = tmp_path / "written.txt"
        output, success = dispatch_tool(
            "file_write", {"path": str(target), "content": "data"}
        )
        assert success is True
        assert target.read_text() == "data"

    def test_unknown_tool(self):
        output, success = dispatch_tool("nonexistent_tool", {})
        assert success is False
        assert "Unknown tool" in output

    @patch("system_tender.engine.urllib.request.urlopen")
    def test_http_dispatch(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        output, success = dispatch_tool("http_request", {"url": "https://example.com"})
        assert success is True
        assert "status: 200" in output


class TestBuildSystemPrompt:
    def test_contains_tool_names(self, sample_task_config, sample_global_config):
        prompt = build_system_prompt(sample_task_config, sample_global_config)
        assert "shell" in prompt
        assert "file_read" in prompt

    def test_contains_timeout(self, sample_task_config, sample_global_config):
        prompt = build_system_prompt(sample_task_config, sample_global_config)
        assert str(sample_task_config.timeout) in prompt

    def test_uses_default_system_prompt(self, sample_task_config, sample_global_config):
        prompt = build_system_prompt(sample_task_config, sample_global_config)
        assert "system maintenance agent" in prompt

    def test_uses_custom_system_prompt(self, sample_global_config):
        task = TaskConfig(
            name="custom",
            system_prompt="You are a custom bot",
        )
        prompt = build_system_prompt(task, sample_global_config)
        assert "custom bot" in prompt
        assert "system maintenance agent" not in prompt


class TestBuildToolList:
    def test_returns_correct_tools(self, sample_task_config):
        tools = build_tool_list(sample_task_config)
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert "shell_execute" in names
        assert "file_read" in names

    def test_all_tools(self):
        task = TaskConfig(
            name="all",
            allowed_tools=list(ToolName),
        )
        tools = build_tool_list(task)
        assert len(tools) == 4

    def test_empty_tools(self):
        task = TaskConfig(name="none", allowed_tools=[])
        tools = build_tool_list(task)
        assert tools == []

    def test_tool_definitions_have_schemas(self):
        for tool_name, defn in TOOL_DEFINITIONS.items():
            assert "name" in defn
            assert "description" in defn
            assert "input_schema" in defn
