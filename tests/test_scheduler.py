"""Tests for system_tender.scheduler."""

import platform

import pytest

from system_tender.scheduler import (
    detect_scheduler,
    generate_crontab_entry,
    generate_launchd_plist,
    generate_systemd_units,
    parse_cron,
)


class TestParseCron:
    def test_daily_at_6am(self):
        result = parse_cron("0 6 * * *")
        assert result == {"minute": "0", "hour": "6"}

    def test_every_monday(self):
        result = parse_cron("0 0 * * 1")
        assert result == {"minute": "0", "hour": "0", "weekday": "1"}

    def test_all_wildcards(self):
        result = parse_cron("* * * * *")
        assert result == {}

    def test_all_fields_specified(self):
        result = parse_cron("30 14 1 6 3")
        assert result == {
            "minute": "30",
            "hour": "14",
            "day": "1",
            "month": "6",
            "weekday": "3",
        }

    def test_too_few_fields(self):
        with pytest.raises(ValueError, match="Expected 5-field"):
            parse_cron("0 6 *")

    def test_too_many_fields(self):
        with pytest.raises(ValueError, match="Expected 5-field"):
            parse_cron("0 6 * * * *")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Expected 5-field"):
            parse_cron("")

    def test_strips_whitespace(self):
        result = parse_cron("  0 6 * * *  ")
        assert result == {"minute": "0", "hour": "6"}


class TestGenerateLaunchdPlist:
    def test_produces_valid_xml(self):
        plist = generate_launchd_plist("test-task", "0 6 * * *")
        assert '<?xml version="1.0"' in plist
        assert "<!DOCTYPE plist" in plist
        assert "<plist" in plist

    def test_contains_label(self):
        plist = generate_launchd_plist("my-task", "0 6 * * *")
        assert "com.system-tender.my-task" in plist

    def test_contains_program_arguments(self):
        plist = generate_launchd_plist("my-task", "0 6 * * *")
        assert "ProgramArguments" in plist
        assert "tender" in plist
        assert "my-task" in plist

    def test_contains_calendar_interval(self):
        plist = generate_launchd_plist("my-task", "0 6 * * *")
        assert "StartCalendarInterval" in plist
        assert "<key>Hour</key>" in plist
        assert "<integer>6</integer>" in plist
        assert "<key>Minute</key>" in plist
        assert "<integer>0</integer>" in plist

    def test_contains_logging_paths(self):
        plist = generate_launchd_plist("my-task", "0 6 * * *")
        assert "StandardOutPath" in plist
        assert "StandardErrorPath" in plist
        assert "my-task.stdout.log" in plist

    def test_env_variables(self):
        plist = generate_launchd_plist(
            "env-task", "0 6 * * *", env={"API_KEY": "secret123"}
        )
        assert "EnvironmentVariables" in plist
        assert "API_KEY" in plist
        assert "secret123" in plist

    def test_step_values_raise(self):
        with pytest.raises(ValueError, match="step values"):
            generate_launchd_plist("step-task", "*/5 * * * *")


class TestGenerateSystemdUnits:
    def test_returns_service_and_timer(self):
        service, timer = generate_systemd_units("my-task", "0 6 * * *")
        assert isinstance(service, str)
        assert isinstance(timer, str)

    def test_service_has_exec_start(self):
        service, _ = generate_systemd_units("my-task", "0 6 * * *")
        assert "ExecStart=" in service
        assert "tender" in service
        assert "my-task" in service

    def test_service_is_oneshot(self):
        service, _ = generate_systemd_units("my-task", "0 6 * * *")
        assert "Type=oneshot" in service

    def test_timer_has_oncalendar(self):
        _, timer = generate_systemd_units("my-task", "0 6 * * *")
        assert "OnCalendar=" in timer
        assert "06:00:00" in timer

    def test_timer_is_persistent(self):
        _, timer = generate_systemd_units("my-task", "0 6 * * *")
        assert "Persistent=true" in timer

    def test_env_in_service(self):
        service, _ = generate_systemd_units(
            "env-task", "0 6 * * *", env={"FOO": "bar"}
        )
        assert "Environment=FOO=bar" in service

    def test_weekday_in_timer(self):
        _, timer = generate_systemd_units("mon-task", "0 0 * * 1")
        assert "Mon" in timer


class TestGenerateCrontabEntry:
    def test_basic_entry(self):
        entry = generate_crontab_entry("my-task", "0 6 * * *")
        assert entry.startswith("0 6 * * *")
        assert "tender" in entry
        assert "my-task" in entry

    def test_with_env(self):
        entry = generate_crontab_entry(
            "env-task", "0 6 * * *", env={"FOO": "bar", "BAZ": "qux"}
        )
        assert "FOO=bar" in entry
        assert "BAZ=qux" in entry
        assert "tender" in entry

    def test_preserves_schedule(self):
        entry = generate_crontab_entry("x", "*/5 * * * *")
        assert entry.startswith("*/5 * * * *")


class TestDetectScheduler:
    def test_returns_valid_string(self):
        result = detect_scheduler()
        assert result in ("launchd", "systemd", "cron")

    def test_darwin_returns_launchd(self):
        if platform.system() == "Darwin":
            assert detect_scheduler() == "launchd"
