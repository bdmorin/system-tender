"""Tests for system_tender.models."""

from datetime import datetime, timezone

from system_tender.models import (
    GlobalConfig,
    OutputFormat,
    TaskConfig,
    TaskPrompt,
    TaskResult,
    ToolCall,
    ToolName,
)


class TestToolNameEnum:
    def test_values(self):
        assert ToolName.SHELL.value == "shell"
        assert ToolName.FILE_READ.value == "file_read"
        assert ToolName.FILE_WRITE.value == "file_write"
        assert ToolName.HTTP_REQUEST.value == "http_request"

    def test_from_string(self):
        assert ToolName("shell") is ToolName.SHELL
        assert ToolName("file_read") is ToolName.FILE_READ


class TestOutputFormatEnum:
    def test_values(self):
        assert OutputFormat.TEXT.value == "text"
        assert OutputFormat.STRUCTURED.value == "structured"


class TestTaskPrompt:
    def test_simple(self):
        p = TaskPrompt(text="do stuff")
        assert p.text == "do stuff"
        assert p.context_files == []

    def test_with_context_files(self):
        p = TaskPrompt(text="read logs", context_files=["/var/log/syslog"])
        assert p.context_files == ["/var/log/syslog"]


class TestTaskConfig:
    def test_minimal(self):
        t = TaskConfig(name="hello")
        assert t.name == "hello"
        assert t.description == ""
        assert t.timeout == 300
        assert t.allowed_tools == [ToolName.SHELL, ToolName.FILE_READ]
        assert t.output_format == OutputFormat.TEXT
        assert t.schedule is None
        assert t.env == {}
        assert t.network_access is False
        assert t.egress_allowlist == []

    def test_prompt_text_string(self):
        t = TaskConfig(name="x", prompt="do it")
        assert t.prompt_text == "do it"

    def test_prompt_text_object(self):
        t = TaskConfig(name="x", prompt=TaskPrompt(text="do it properly"))
        assert t.prompt_text == "do it properly"

    def test_custom_tools(self):
        t = TaskConfig(
            name="writer",
            allowed_tools=[ToolName.FILE_WRITE, ToolName.HTTP_REQUEST],
        )
        assert ToolName.FILE_WRITE in t.allowed_tools
        assert ToolName.SHELL not in t.allowed_tools

    def test_env_dict(self):
        t = TaskConfig(name="x", env={"FOO": "bar"})
        assert t.env["FOO"] == "bar"

    def test_network_access_fields(self):
        t = TaskConfig(
            name="net",
            network_access=True,
            egress_allowlist=["api.github.com", "*.slack.com"],
        )
        assert t.network_access is True
        assert t.egress_allowlist == ["api.github.com", "*.slack.com"]


class TestGlobalConfig:
    def test_defaults(self):
        g = GlobalConfig()
        assert g.model == "claude-sonnet-4-6"
        assert g.max_tokens == 4096
        assert g.default_timeout == 300
        assert g.api_key_env == "ANTHROPIC_API_KEY"

    def test_tasks_dir(self, tmp_path):
        g = GlobalConfig(config_dir=tmp_path)
        assert g.tasks_dir == tmp_path / "tasks"

    def test_runs_dir(self, tmp_path):
        g = GlobalConfig(config_dir=tmp_path)
        assert g.runs_dir == tmp_path / "runs"

    def test_effective_log_dir_default(self, tmp_path):
        g = GlobalConfig(config_dir=tmp_path)
        assert g.effective_log_dir == tmp_path / "logs"

    def test_effective_log_dir_override(self, tmp_path):
        custom = tmp_path / "my-logs"
        g = GlobalConfig(config_dir=tmp_path, log_dir=custom)
        assert g.effective_log_dir == custom


class TestToolCall:
    def test_creation(self):
        tc = ToolCall(
            tool_name="shell_execute",
            input={"command": "ls"},
            output="file.txt",
            duration_ms=42,
            success=True,
        )
        assert tc.tool_name == "shell_execute"
        assert tc.input == {"command": "ls"}
        assert tc.output == "file.txt"
        assert tc.duration_ms == 42
        assert tc.success is True

    def test_defaults(self):
        tc = ToolCall(tool_name="x", input={}, output="ok")
        assert tc.duration_ms == 0
        assert tc.success is True


class TestTaskResult:
    def test_defaults(self):
        r = TaskResult(task_name="test")
        assert r.task_name == "test"
        assert r.success is False
        assert r.output == ""
        assert r.error is None
        assert r.tool_calls == []
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert len(r.run_id) == 12

    def test_to_summary_success(self):
        r = TaskResult(
            task_name="disk-check",
            success=True,
            duration_ms=1500,
            input_tokens=100,
            output_tokens=50,
            output="All good",
        )
        s = r.to_summary()
        assert "[OK]" in s
        assert "disk-check" in s
        assert "1.5s" in s
        assert "150 tokens" in s
        assert "0 tool calls" in s
        # Output is printed separately by CLI, not included in summary
        assert "All good" not in s

    def test_to_summary_failure_with_error(self):
        r = TaskResult(
            task_name="broken",
            success=False,
            error="something went wrong",
            duration_ms=200,
        )
        s = r.to_summary()
        assert "[FAILED]" in s
        assert "Error: something went wrong" in s

    def test_to_summary_excludes_output(self):
        """Summary is just status — output is printed separately by CLI."""
        r = TaskResult(
            task_name="verbose",
            success=True,
            output="x" * 300,
            duration_ms=0,
        )
        s = r.to_summary()
        assert "xxx" not in s

    def test_to_summary_with_tool_calls(self):
        r = TaskResult(
            task_name="tools",
            success=True,
            duration_ms=0,
            tool_calls=[
                ToolCall(tool_name="a", input={}, output="ok"),
                ToolCall(tool_name="b", input={}, output="ok"),
            ],
        )
        s = r.to_summary()
        assert "2 tool calls" in s
