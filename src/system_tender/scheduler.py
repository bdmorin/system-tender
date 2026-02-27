"""Scheduler generators for system-tender.

Generates platform-native scheduled task configurations from cron-style
schedules. Supports macOS launchd, Linux systemd, and plain crontab.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import textwrap
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString


# ---------------------------------------------------------------------------
# Cron schedule parsing
# ---------------------------------------------------------------------------

CRON_FIELDS = ("minute", "hour", "day", "month", "weekday")

# Maps cron field names to launchd StartCalendarInterval keys
LAUNCHD_KEY_MAP = {
    "minute": "Minute",
    "hour": "Hour",
    "day": "Day",
    "month": "Month",
    "weekday": "Weekday",
}

# Maps cron field names to systemd OnCalendar positional tokens.
# systemd format: DayOfWeek Year-Month-Day Hour:Minute:Second
# We build it piece by piece.


def parse_cron(schedule: str) -> dict[str, str]:
    """Parse a 5-field cron expression into a dict keyed by field name.

    Returns only fields that are not wildcards (*).
    """
    parts = schedule.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Expected 5-field cron expression (minute hour day month weekday), got {len(parts)}: {schedule!r}"
        )
    result: dict[str, str] = {}
    for name, value in zip(CRON_FIELDS, parts):
        if value != "*":
            result[name] = value
    return result


# ---------------------------------------------------------------------------
# Resolve the tender command
# ---------------------------------------------------------------------------

def _tender_command(task_name: str) -> str:
    """Return the full command string for running a task."""
    tender_path = shutil.which("tender")
    if tender_path:
        return f"{tender_path} run {task_name}"
    return f"tender run {task_name}"


# ---------------------------------------------------------------------------
# launchd plist generation (macOS)
# ---------------------------------------------------------------------------

_LOG_DIR = Path.home() / ".config" / "system-tender" / "logs"
_PLIST_DIR = Path.home() / "Library" / "LaunchAgents"


def _build_calendar_interval(parsed: dict[str, str]) -> dict[str, int]:
    """Convert parsed cron fields to a launchd StartCalendarInterval dict."""
    interval: dict[str, int] = {}
    for field, value in parsed.items():
        key = LAUNCHD_KEY_MAP.get(field)
        if key is None:
            continue
        # Handle step values like */5 -> not directly supported by launchd,
        # but we can handle simple integers.
        if "/" in value:
            # launchd doesn't natively support step intervals in
            # StartCalendarInterval.  For */N minute intervals we'd need
            # multiple entries.  For this spike, warn and skip.
            raise ValueError(
                f"launchd StartCalendarInterval does not support step values ({value}). "
                "Use explicit cron values or switch to crontab."
            )
        interval[key] = int(value)
    return interval


def generate_launchd_plist(
    task_name: str,
    schedule: str,
    env: dict[str, str] | None = None,
) -> str:
    """Generate a launchd plist XML string for the given task."""
    parsed = parse_cron(schedule)
    calendar = _build_calendar_interval(parsed)
    label = f"com.system-tender.{task_name}"
    command = _tender_command(task_name)

    plist = Element("plist", version="1.0")
    d = SubElement(plist, "dict")

    def add_key_string(parent: Element, key: str, value: str) -> None:
        SubElement(parent, "key").text = key
        SubElement(parent, "string").text = value

    def add_key_int(parent: Element, key: str, value: int) -> None:
        SubElement(parent, "key").text = key
        SubElement(parent, "integer").text = str(value)

    # Label
    add_key_string(d, "Label", label)

    # ProgramArguments
    SubElement(d, "key").text = "ProgramArguments"
    args_array = SubElement(d, "array")
    for part in command.split():
        SubElement(args_array, "string").text = part

    # StartCalendarInterval
    SubElement(d, "key").text = "StartCalendarInterval"
    cal_dict = SubElement(d, "dict")
    for k, v in calendar.items():
        add_key_int(cal_dict, k, v)

    # Logging
    log_dir = _LOG_DIR
    add_key_string(d, "StandardOutPath", str(log_dir / f"{task_name}.stdout.log"))
    add_key_string(d, "StandardErrorPath", str(log_dir / f"{task_name}.stderr.log"))

    # Environment variables
    if env:
        SubElement(d, "key").text = "EnvironmentVariables"
        env_dict = SubElement(d, "dict")
        for ek, ev in env.items():
            add_key_string(env_dict, ek, ev)

    # Format the XML
    raw_xml = tostring(plist, encoding="unicode")
    dom = parseString(raw_xml)
    pretty = dom.toprettyxml(indent="  ", encoding=None)

    # Add the DOCTYPE that launchd expects, strip the default xml declaration
    lines = pretty.split("\n")
    # Remove <?xml ...?> line if present
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    )
    return header + "\n" + "\n".join(lines).strip() + "\n"


def install_launchd(
    task_name: str,
    schedule: str,
    env: dict[str, str] | None = None,
) -> Path:
    """Write a launchd plist to ~/Library/LaunchAgents and return its path."""
    plist_content = generate_launchd_plist(task_name, schedule, env)
    label = f"com.system-tender.{task_name}"

    # Ensure directories exist
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _PLIST_DIR.mkdir(parents=True, exist_ok=True)

    plist_path = _PLIST_DIR / f"{label}.plist"
    plist_path.write_text(plist_content, encoding="utf-8")
    return plist_path


# ---------------------------------------------------------------------------
# systemd unit generation (Linux)
# ---------------------------------------------------------------------------

def _cron_to_oncalendar(parsed: dict[str, str]) -> str:
    """Convert parsed cron fields to a systemd OnCalendar expression.

    systemd format: DayOfWeek Year-Month-Day Hour:Minute:Second
    Examples:
        daily at 6am  -> *-*-* 06:00:00
        hourly        -> *-*-* *:00:00
        every Monday  -> Mon *-*-* 00:00:00
    """
    weekday_map = {
        "0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
        "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun",
    }

    dow = ""
    if "weekday" in parsed:
        raw = parsed["weekday"]
        dow = weekday_map.get(raw, raw) + " "

    month = parsed.get("month", "*")
    day = parsed.get("day", "*")
    hour = parsed.get("hour", "*")
    minute = parsed.get("minute", "*")

    # Zero-pad numeric values for readability
    if hour.isdigit():
        hour = hour.zfill(2)
    if minute.isdigit():
        minute = minute.zfill(2)
    if month.isdigit():
        month = month.zfill(2)
    if day.isdigit():
        day = day.zfill(2)

    # Handle step values: */5 -> translate to systemd range notation
    # systemd understands *:00/5 for "every 5 minutes"
    if "/" in minute:
        # e.g. */5 -> 00/5
        minute = minute.replace("*", "00")

    return f"{dow}*-{month}-{day} {hour}:{minute}:00"


def generate_systemd_units(
    task_name: str,
    schedule: str,
    env: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Generate systemd service and timer unit file contents.

    Returns (service_content, timer_content).
    """
    parsed = parse_cron(schedule)
    oncalendar = _cron_to_oncalendar(parsed)
    command = _tender_command(task_name)

    service_lines = [
        "[Unit]",
        f"Description=system-tender task: {task_name}",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart={command}",
    ]
    if env:
        for k, v in env.items():
            service_lines.append(f"Environment={k}={v}")
    service_lines += [
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ]
    service = "\n".join(service_lines) + "\n"

    timer = textwrap.dedent(f"""\
        [Unit]
        Description=Timer for system-tender task: {task_name}

        [Timer]
        OnCalendar={oncalendar}
        Persistent=true

        [Install]
        WantedBy=timers.target
    """)

    return service, timer


# ---------------------------------------------------------------------------
# crontab entry generation
# ---------------------------------------------------------------------------

def generate_crontab_entry(
    task_name: str,
    schedule: str,
    env: dict[str, str] | None = None,
) -> str:
    """Generate a crontab entry string for the given task."""
    command = _tender_command(task_name)
    env_prefix = ""
    if env:
        env_prefix = " ".join(f"{k}={v}" for k, v in env.items()) + " "
    return f"{schedule} {env_prefix}{command}"


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_scheduler() -> str:
    """Detect the best available scheduler for the current platform.

    Returns "launchd", "systemd", or "cron".
    """
    system = platform.system()

    if system == "Darwin":
        return "launchd"

    if system == "Linux":
        # Check for systemd by looking for systemctl
        if shutil.which("systemctl"):
            try:
                result = subprocess.run(
                    ["systemctl", "--version"],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return "systemd"
            except (subprocess.TimeoutExpired, OSError):
                pass

    # Fallback: cron is available nearly everywhere
    return "cron"
