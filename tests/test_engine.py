"""Tests for system_tender.engine (tool execution, NOT API calls)."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from system_tender.engine import (
    MAX_SHELL_TIMEOUT,
    TOOL_DEFINITIONS,
    _redact_tool_input,
    build_system_prompt,
    build_tool_list,
    check_egress_allowed,
    dispatch_tool,
    execute_file_read,
    execute_file_write,
    execute_http_request,
    execute_notify,
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
        assert len(tools) == len(ToolName)

    def test_empty_tools(self):
        task = TaskConfig(name="none", allowed_tools=[])
        tools = build_tool_list(task)
        assert tools == []

    def test_tool_definitions_have_schemas(self):
        for tool_name, defn in TOOL_DEFINITIONS.items():
            assert "name" in defn
            assert "description" in defn
            assert "input_schema" in defn


class TestExecuteNotify:
    @patch("system_tender.engine.platform.system", return_value="Darwin")
    @patch("system_tender.engine.subprocess.run")
    def test_macos_notification(self, mock_run, mock_system):
        result = execute_notify("Test Title", "Test message")
        assert "OK:" in result
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][0][0] == "osascript"
        assert "Test Title" in args[0][0][2]
        assert "Test message" in args[0][0][2]
        assert 'sound name "default"' in args[0][0][2]

    @patch("system_tender.engine.platform.system", return_value="Darwin")
    @patch("system_tender.engine.subprocess.run")
    def test_macos_no_sound(self, mock_run, mock_system):
        result = execute_notify("Title", "Body", sound=False)
        assert "OK:" in result
        script = mock_run.call_args[0][0][2]
        assert "sound name" not in script

    @patch("system_tender.engine.platform.system", return_value="Darwin")
    @patch("system_tender.engine.subprocess.run")
    def test_macos_escapes_quotes(self, mock_run, mock_system):
        result = execute_notify('He said "hello"', 'It\'s a "test"')
        assert "OK:" in result
        script = mock_run.call_args[0][0][2]
        assert '\\"hello\\"' in script
        assert '\\"test\\"' in script

    @patch("system_tender.engine.platform.system", return_value="Linux")
    @patch("system_tender.engine.shutil.which", return_value="/usr/bin/notify-send")
    @patch("system_tender.engine.subprocess.run")
    def test_linux_notification(self, mock_run, mock_which, mock_system):
        result = execute_notify("Linux Title", "Linux message")
        assert "OK:" in result
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["notify-send", "Linux Title", "Linux message"]

    @patch("system_tender.engine.platform.system", return_value="Linux")
    @patch("system_tender.engine.shutil.which", return_value=None)
    def test_linux_missing_notify_send(self, mock_which, mock_system):
        result = execute_notify("Title", "Message")
        assert "ERROR:" in result
        assert "notify-send not found" in result

    @patch("system_tender.engine.platform.system", return_value="Windows")
    def test_unsupported_platform(self, mock_system):
        result = execute_notify("Title", "Message")
        assert "ERROR:" in result
        assert "not supported" in result


class TestDispatchNotify:
    @patch("system_tender.engine.platform.system", return_value="Darwin")
    @patch("system_tender.engine.subprocess.run")
    def test_dispatch_routes_to_notify(self, mock_run, mock_system):
        output, success = dispatch_tool(
            "notify", {"title": "Dispatch Test", "message": "Hello"}
        )
        assert success is True
        assert "OK:" in output


class TestCheckEgressAllowed:
    def test_no_task_allows_all(self):
        assert check_egress_allowed("https://example.com", None) is None

    def test_network_access_false_denies(self):
        task = TaskConfig(name="locked", network_access=False)
        result = check_egress_allowed("https://example.com", task)
        assert result is not None
        assert "Network access denied" in result

    def test_network_access_true_no_allowlist_allows_all(self):
        task = TaskConfig(name="open", network_access=True)
        assert check_egress_allowed("https://example.com", task) is None

    def test_network_access_true_wildcard_allows_all(self):
        task = TaskConfig(name="star", network_access=True, egress_allowlist=["*"])
        assert check_egress_allowed("https://anything.example.com", task) is None

    def test_exact_host_match(self):
        task = TaskConfig(
            name="exact",
            network_access=True,
            egress_allowlist=["api.github.com"],
        )
        assert check_egress_allowed("https://api.github.com/repos", task) is None

    def test_exact_host_mismatch(self):
        task = TaskConfig(
            name="exact",
            network_access=True,
            egress_allowlist=["api.github.com"],
        )
        result = check_egress_allowed("https://evil.example.com/steal", task)
        assert result is not None
        assert "Egress denied" in result
        assert "evil.example.com" in result
        assert "api.github.com" in result

    def test_wildcard_subdomain_match(self):
        task = TaskConfig(
            name="wild",
            network_access=True,
            egress_allowlist=["*.github.com"],
        )
        assert check_egress_allowed("https://api.github.com/repos", task) is None
        assert check_egress_allowed("https://raw.github.com/file", task) is None

    def test_wildcard_subdomain_mismatch(self):
        task = TaskConfig(
            name="wild",
            network_access=True,
            egress_allowlist=["*.github.com"],
        )
        result = check_egress_allowed("https://github.io/page", task)
        assert result is not None
        assert "Egress denied" in result

    def test_multiple_allowlist_entries(self):
        task = TaskConfig(
            name="multi",
            network_access=True,
            egress_allowlist=["api.github.com", "*.slack.com", "hooks.example.org"],
        )
        assert check_egress_allowed("https://api.github.com/v1", task) is None
        assert check_egress_allowed("https://hooks.slack.com/webhook", task) is None
        assert check_egress_allowed("https://hooks.example.org/cb", task) is None
        result = check_egress_allowed("https://evil.com", task)
        assert result is not None
        assert "Egress denied" in result


class TestDispatchToolNetworkAccess:
    """Test that dispatch_tool enforces network access policy for http_request."""

    def test_http_denied_when_network_access_false(self):
        task = TaskConfig(
            name="no-net",
            network_access=False,
            allowed_tools=[ToolName.HTTP_REQUEST],
        )
        output, success = dispatch_tool(
            "http_request", {"url": "https://example.com"}, task=task
        )
        assert success is False
        assert "Network access denied" in output

    @patch("system_tender.engine.urllib.request.urlopen")
    def test_http_allowed_when_network_access_true(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        task = TaskConfig(
            name="open-net",
            network_access=True,
            allowed_tools=[ToolName.HTTP_REQUEST],
        )
        output, success = dispatch_tool(
            "http_request", {"url": "https://example.com"}, task=task
        )
        assert success is True
        assert "status: 200" in output

    def test_http_denied_by_egress_allowlist(self):
        task = TaskConfig(
            name="filtered",
            network_access=True,
            egress_allowlist=["api.github.com"],
            allowed_tools=[ToolName.HTTP_REQUEST],
        )
        output, success = dispatch_tool(
            "http_request", {"url": "https://evil.example.com/steal"}, task=task
        )
        assert success is False
        assert "Egress denied" in output

    @patch("system_tender.engine.urllib.request.urlopen")
    def test_http_allowed_by_egress_allowlist(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        task = TaskConfig(
            name="filtered",
            network_access=True,
            egress_allowlist=["api.github.com"],
            allowed_tools=[ToolName.HTTP_REQUEST],
        )
        output, success = dispatch_tool(
            "http_request", {"url": "https://api.github.com/repos"}, task=task
        )
        assert success is True
        assert "status: 200" in output

    def test_shell_not_affected_by_network_policy(self):
        """Shell commands are NOT restricted by network_access — documented limitation."""
        task = TaskConfig(
            name="no-net",
            network_access=False,
            allowed_tools=[ToolName.SHELL],
        )
        output, success = dispatch_tool(
            "shell_execute", {"command": "echo still works"}, task=task
        )
        assert success is True
        assert "still works" in output

    def test_no_task_passed_allows_http(self):
        """Backward compat: dispatch_tool without task param allows http_request."""
        with patch("system_tender.engine.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {}
            mock_resp.read.return_value = b"ok"
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            output, success = dispatch_tool(
                "http_request", {"url": "https://example.com"}
            )
            assert success is True


# --- Security Tests ---


class TestRedactToolInput:
    def test_redacts_authorization_header(self):
        result = _redact_tool_input("http_request", {
            "url": "https://example.com",
            "headers": {
                "Authorization": "Bearer sk-secret-key-123",
                "Content-Type": "application/json",
            },
        })
        assert result["headers"]["Authorization"] == "[REDACTED]"
        assert result["headers"]["Content-Type"] == "application/json"
        assert result["url"] == "https://example.com"

    def test_redacts_multiple_sensitive_headers(self):
        result = _redact_tool_input("http_request", {
            "url": "https://example.com",
            "headers": {
                "Authorization": "Bearer token",
                "X-Api-Key": "key-123",
                "Cookie": "session=abc",
                "Accept": "application/json",
            },
        })
        assert result["headers"]["Authorization"] == "[REDACTED]"
        assert result["headers"]["X-Api-Key"] == "[REDACTED]"
        assert result["headers"]["Cookie"] == "[REDACTED]"
        assert result["headers"]["Accept"] == "application/json"

    def test_no_headers_unchanged(self):
        result = _redact_tool_input("http_request", {"url": "https://example.com"})
        assert result == {"url": "https://example.com"}

    def test_non_http_tool_unchanged(self):
        inp = {"command": "echo secret-key-123"}
        result = _redact_tool_input("shell_execute", inp)
        assert result == inp

    def test_does_not_mutate_original(self):
        original = {
            "url": "https://example.com",
            "headers": {"Authorization": "Bearer secret"},
        }
        _redact_tool_input("http_request", original)
        assert original["headers"]["Authorization"] == "Bearer secret"


class TestShellTimeoutCap:
    def test_huge_timeout_capped(self):
        result = execute_shell("echo hello", timeout=999999)
        assert "hello" in result
        assert "exit_code: 0" in result

    def test_negative_timeout_clamped_to_minimum(self):
        result = execute_shell("echo hello", timeout=-5)
        assert "hello" in result
        assert "exit_code: 0" in result

    def test_zero_timeout_clamped_to_minimum(self):
        result = execute_shell("echo hello", timeout=0)
        assert "hello" in result

    def test_max_shell_timeout_constant(self):
        assert MAX_SHELL_TIMEOUT == 3600


class TestLoadEnvAllowlist:
    def test_blocks_non_anthropic_vars(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Also record PATH so monkeypatch restores it on failure
        monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))

        env_file = tmp_path / ".env"
        env_file.write_text("PATH=/evil\nANTHROPIC_API_KEY=sk-test-key\n")
        os.chmod(env_file, 0o600)

        original_path = os.environ["PATH"]

        from system_tender.engine import _load_env
        _load_env(tmp_path)

        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-test-key"
        assert os.environ["PATH"] == original_path

    def test_loads_anthropic_prefixed_vars(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_LOG", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text(
            "ANTHROPIC_API_KEY=sk-key\n"
            "ANTHROPIC_LOG=debug\n"
        )
        os.chmod(env_file, 0o600)

        from system_tender.engine import _load_env
        _load_env(tmp_path)

        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-key"
        assert os.environ.get("ANTHROPIC_LOG") == "debug"

    def test_skips_if_api_key_already_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "already-set")

        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=should-not-load\n")

        from system_tender.engine import _load_env
        _load_env(tmp_path)

        assert os.environ["ANTHROPIC_API_KEY"] == "already-set"


class TestRunFilePermissions:
    def test_saved_run_has_restrictive_permissions(self, tmp_path):
        from system_tender.engine import save_run
        from system_tender.models import GlobalConfig, TaskResult

        config = GlobalConfig(config_dir=tmp_path)
        result = TaskResult(task_name="perm-test", success=True)

        path = save_run(result, config)
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600
