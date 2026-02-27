# system-tender Menubar App — Full Specification

**Confidence**: MEDIUM-HIGH
**Date**: 2026-02-27
**Author**: Research Agent

---

## Technology Evaluation Summary

| Option | Complexity | Maintenance | File Watch | Standalone | macOS Survival | Verdict |
|---|---|---|---|---|---|---|
| **SwiftBar plugin** | 1/5 | 1/5 | Yes (streamable) | No (needs SwiftBar) | High | **Recommended** |
| rumps + watchdog | 2/5 | 3/5 | Yes (watchdog) | Yes (.app via py2app) | Medium | Good backup |
| py2app + PyObjC | 4/5 | 4/5 | Yes (watchdog) | Yes | Medium | Over-engineered |
| Swift/SwiftUI | 5/5 | 3/5 | Yes (FSEvents) | Yes | High | Wrong skillset |
| Tauri | 4/5 | 3/5 | Yes | Yes | High | Overkill (Rust+JS) |

### Why SwiftBar Plugin Wins

1. **One Python file.** No build step, no packaging, no Xcode, no .app bundle
2. **SwiftBar is actively maintained** — 3.8k+ GitHub stars, Homebrew-installable, recent releases
3. **Streamable plugin mode** — Long-running process that pushes updates in real-time using `~~~` separator
4. **Rich formatting** — SF Symbols, colors, submenus, clickable items that run scripts
5. **Python** — Same language as system-tender, can reuse models/logic
6. **Zero maintenance** — SwiftBar handles all native macOS integration, survives OS updates
7. **An autonomous agent can build this in one file** — trivially specifiable

### Tradeoffs Accepted

- Requires SwiftBar installed (`brew install --cask swiftbar`) — acceptable dependency
- Menu-only UI (no popup windows or rich views) — sufficient for status display
- No notification support from plugin itself — system-tender already has `notify` tool

---

## Architecture

### Data Flow

```
~/.config/system-tender/runs/*.json
        |
        | (watchdog FSEvents or polling)
        v
  SwiftBar Plugin (Python, long-running streamable)
        |
        | (stdout with SwiftBar format)
        v
    SwiftBar → macOS Menubar
        |
        | (bash= parameter on menu items)
        v
  `tender run <task>` (subprocess)
```

### Run File Schema (from actual data)

Each JSON file in `~/.config/system-tender/runs/` contains:

```json
{
  "run_id": "7e309aaa71c4",
  "task_name": "system-check",
  "started_at": "2026-02-27T16:06:11.285511Z",
  "completed_at": "2026-02-27T16:06:33.101218Z",
  "success": true,
  "output": "...",
  "error": null,
  "tool_calls": [...],
  "input_tokens": 4731,
  "output_tokens": 1125,
  "model": "claude-sonnet-4-6",
  "duration_ms": 21815
}
```

Filename pattern: `YYYYMMDD-HHMMSS-{task_name}-{run_id}.json`

---

## File Structure

```
system-tender/
└── menubar/
    ├── system-tender-status.1m.py    # The SwiftBar plugin (main file)
    ├── install.sh                     # Installation script
    └── README.md                      # Usage instructions (only if requested)
```

That's it. One Python script file, one install script.

---

## SwiftBar Plugin Specification

### File: `menubar/system-tender-status.1m.py`

**Naming**: The `1m` means SwiftBar runs it every 1 minute as fallback. But since we use streamable mode, the plugin runs continuously and pushes updates via `~~~`.

**Runtime**: Uses system Python 3 (`#!/usr/bin/env python3`). No virtualenv, no pip install needed — only stdlib.

### Metadata Header

```python
#!/usr/bin/env python3

# <xbar.title>system-tender Status</xbar.title>
# <xbar.version>v0.1.0</xbar.version>
# <xbar.author>system-tender</xbar.author>
# <xbar.author.github>bdmorin</xbar.author.github>
# <xbar.desc>Shows system-tender task run status in the menubar</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>

# <swiftbar.type>streamable</swiftbar.type>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideSwiftBar>false</swiftbar.hideSwiftBar>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
```

### Configuration Constants

```python
import json
import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# Paths
RUNS_DIR = Path.home() / ".config" / "system-tender" / "runs"
TASKS_DIR = Path.home() / ".config" / "system-tender" / "tasks"
TENDER_BIN = "tender"  # Assumes tender is on PATH

# Display settings
MAX_RECENT_RUNS = 15
MAX_OUTPUT_LINES = 20
POLL_INTERVAL_SECONDS = 10  # How often to check for changes
```

### Core Logic

The plugin operates in a continuous loop:

1. **Read all run files** from `RUNS_DIR`, sorted by modification time (newest first)
2. **Group by task name** — show latest run per task, plus recent history
3. **Format output** using SwiftBar's stdout protocol
4. **Print `~~~`** to signal SwiftBar to refresh the menu
5. **Sleep** and repeat (with filesystem polling for changes)

### Menu Structure (ASCII Mockup)

```
 [icon] ST: 3/3         ← menubar title: success count / total recent
 ─────────────────────────────────────────
 Last Run: 2m ago                        ← time since most recent run
 ─────────────────────────────────────────
 Tasks                                    ← section header
   ✓ system-check      21s    2m ago     ← green checkmark, duration, age
   ✓ disk-report       1m55s  30m ago
   ✗ brew-update       45s    2h ago     ← red X for failure
 ─────────────────────────────────────────
 Recent Runs                              ← section header
   ✓ system-check  16:06  21s  4.7k/1.1k ← time, duration, tokens in/out
     -- Output (first 20 lines)           ← submenu with output preview
     -- Error: none
     -- Model: claude-sonnet-4-6
     -- Tools: 5 calls
   ✓ disk-report   16:02  1m55s  12k/1.7k
     -- Output (first 20 lines)
     -- ...
   ✓ adhoc          16:05  15s
     -- ...
 ─────────────────────────────────────────
 Run Task...                              ← section header
   ▶ system-check                         ← click to run
   ▶ disk-report
   ▶ brew-update
 ─────────────────────────────────────────
 Open Runs Folder                         ← opens Finder to runs dir
 Open Config                              ← opens config in editor
 Refresh                                  ← force refresh
```

### Output Format Implementation

```python
def format_duration(ms):
    """Convert milliseconds to human-readable duration."""
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    remaining_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes:.0f}m{remaining_seconds:.0f}s"
    hours = minutes / 60
    remaining_minutes = minutes % 60
    return f"{hours:.0f}h{remaining_minutes:.0f}m"


def format_age(iso_timestamp):
    """Convert ISO timestamp to relative age string."""
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


def load_runs():
    """Load all run files, sorted newest first. Returns list of dicts."""
    if not RUNS_DIR.exists():
        return []
    runs = []
    for path in RUNS_DIR.iterdir():
        if path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text())
            data["_path"] = str(path)
            data["_mtime"] = path.stat().st_mtime
            runs.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    runs.sort(key=lambda r: r.get("_mtime", 0), reverse=True)
    return runs


def get_latest_per_task(runs):
    """Return dict of task_name -> latest run."""
    latest = {}
    for run in runs:
        name = run.get("task_name", "unknown")
        if name not in latest:
            latest[name] = run
    return latest


def get_available_tasks():
    """Read task TOML files to get list of available tasks."""
    if not TASKS_DIR.exists():
        return []
    tasks = []
    for path in TASKS_DIR.iterdir():
        if path.suffix == ".toml":
            # Extract task name from filename (without .toml)
            tasks.append(path.stem)
    return sorted(tasks)


def status_icon(success):
    """Return SF Symbol name for success/failure."""
    if success:
        return "checkmark.circle.fill"
    return "xmark.circle.fill"


def status_color(success):
    """Return color for success/failure."""
    if success:
        return "#4CAF50"  # green
    return "#F44336"  # red


def truncate(text, max_lines=MAX_OUTPUT_LINES):
    """Truncate text to max_lines."""
    if not text:
        return "(empty)"
    lines = text.strip().split("\n")
    if len(lines) <= max_lines:
        return text.strip()
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"


def render_menu(runs):
    """Render the full SwiftBar menu to stdout."""
    latest_per_task = get_latest_per_task(runs)
    recent = runs[:MAX_RECENT_RUNS]

    # Count successes in latest per task
    total = len(latest_per_task)
    successes = sum(1 for r in latest_per_task.values() if r.get("success"))

    # --- HEADER (shown in menubar) ---
    if total == 0:
        print("ST: -- | sfimage=gearshape.2 sfcolor=#888888")
    elif successes == total:
        print(f"ST: {successes}/{total} | sfimage=gearshape.2 sfcolor=#4CAF50")
    else:
        print(f"ST: {successes}/{total} | sfimage=gearshape.2 sfcolor=#F44336")

    # --- BODY (dropdown) ---
    print("---")

    if not runs:
        print("No runs found | color=#888888")
        print("---")
        print(f"Watching: {RUNS_DIR} | color=#888888 size=11")
        print("~~~")
        return

    # Last run age
    newest = runs[0]
    age = format_age(newest.get("completed_at", newest.get("started_at", "")))
    print(f"Last run: {age} | color=#888888 size=11")
    print("---")

    # --- Tasks (latest run per task) ---
    print("Tasks | size=11 color=#888888")
    for name, run in sorted(latest_per_task.items()):
        success = run.get("success", False)
        duration = format_duration(run.get("duration_ms", 0))
        completed = run.get("completed_at", run.get("started_at", ""))
        age_str = format_age(completed) if completed else "?"
        icon = status_icon(success)
        color = status_color(success)

        print(f"{name} | sfimage={icon} sfcolor={color} size=13")
        print(f"--{duration}  ·  {age_str} | color=#888888 size=11")

    print("---")

    # --- Recent Runs (detailed) ---
    print("Recent Runs | size=11 color=#888888")
    for run in recent:
        success = run.get("success", False)
        name = run.get("task_name", "unknown")
        duration = format_duration(run.get("duration_ms", 0))
        icon = status_icon(success)
        color = status_color(success)
        completed = run.get("completed_at", "")
        time_str = ""
        if completed:
            try:
                dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M")
            except ValueError:
                time_str = "?"

        tokens_in = run.get("input_tokens", 0)
        tokens_out = run.get("output_tokens", 0)

        def fmt_tokens(n):
            if n >= 1000:
                return f"{n/1000:.1f}k"
            return str(n)

        token_str = f"{fmt_tokens(tokens_in)}/{fmt_tokens(tokens_out)}"
        print(f"{name}  {time_str}  {duration}  {token_str} | sfimage={icon} sfcolor={color} size=12 font=Menlo")

        # Submenus for run details
        model = run.get("model", "?")
        tool_calls = run.get("tool_calls", [])
        error = run.get("error")
        output = run.get("output", "")

        print(f"--Model: {model} | size=11 color=#888888")
        print(f"--Tools: {len(tool_calls)} calls | size=11 color=#888888")

        if error:
            # Escape pipes in error text for SwiftBar
            safe_error = str(error).replace("|", "/").replace("\n", " ")[:100]
            print(f"--Error: {safe_error} | size=11 color=#F44336")

        # Output preview submenu
        if output:
            print("--Output Preview | size=11 color=#AAAAAA")
            preview = truncate(output, MAX_OUTPUT_LINES)
            for line in preview.split("\n"):
                # Escape pipes and trim for SwiftBar compatibility
                safe = line.replace("|", "/")[:120]
                print(f"----{safe} | size=10 font=Menlo trim=false color=#CCCCCC")

        # Open run file
        run_path = run.get("_path", "")
        if run_path:
            print(f"--Open JSON | bash=/usr/bin/open param0={run_path} terminal=false size=11")

    print("---")

    # --- Run Task (trigger from menu) ---
    available_tasks = get_available_tasks()
    if available_tasks:
        print("Run Task... | size=11 color=#888888")
        for task in available_tasks:
            tender_path = _find_tender_bin()
            print(f"--{task} | sfimage=play.fill sfcolor=#2196F3 bash={tender_path} param0=run param1={task} terminal=true size=12")

    print("---")

    # --- Utility actions ---
    print(f"Open Runs Folder | bash=/usr/bin/open param0={RUNS_DIR} terminal=false size=12")
    config_dir = RUNS_DIR.parent
    print(f"Open Config Folder | bash=/usr/bin/open param0={config_dir} terminal=false size=12")
    print("Refresh | refresh=true size=12")

    # End of menu update
    print("~~~")
    sys.stdout.flush()


def _find_tender_bin():
    """Find the tender binary path."""
    # Check common locations
    candidates = [
        Path.home() / ".local" / "bin" / "tender",
        Path("/opt/homebrew/bin/tender"),
        Path("/usr/local/bin/tender"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # Fall back to PATH lookup
    try:
        result = subprocess.run(
            ["which", "tender"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return TENDER_BIN  # Last resort: hope it's on PATH


def get_dir_signature():
    """Get a quick signature of the runs directory for change detection.
    Returns a tuple of (file_count, newest_mtime) which is cheap to compute.
    """
    if not RUNS_DIR.exists():
        return (0, 0)
    try:
        files = list(RUNS_DIR.iterdir())
        json_files = [f for f in files if f.suffix == ".json"]
        if not json_files:
            return (0, 0)
        newest = max(f.stat().st_mtime for f in json_files)
        return (len(json_files), newest)
    except OSError:
        return (0, 0)


def main():
    """Main loop for streamable SwiftBar plugin."""
    last_signature = None

    # Initial render
    runs = load_runs()
    render_menu(runs)
    last_signature = get_dir_signature()

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)

        current_signature = get_dir_signature()
        if current_signature != last_signature:
            runs = load_runs()
            render_menu(runs)
            last_signature = current_signature


if __name__ == "__main__":
    main()
```

### Key Implementation Details

**Why polling instead of watchdog?**
- Zero dependencies. No pip install needed.
- `get_dir_signature()` is cheap: one `iterdir()` + `stat()` calls
- 10-second polling is perfectly adequate for a cron-like system
- watchdog would require pip install in the SwiftBar plugin context, which is fragile

**Why not `import tomllib` for task config?**
- Reading TOML to list tasks requires tomllib (Python 3.11+) or tomli
- Instead, just list `.toml` filenames from the tasks directory — the filename IS the task name
- Keeps the plugin pure stdlib

**SwiftBar format details:**
- `|` separates the display text from parameters
- `--` prefix creates submenu levels (each `--` = one level deeper)
- `sfimage=` uses Apple SF Symbols (built into macOS)
- `sfcolor=` colors the SF Symbol
- `bash=` runs a script when clicked
- `terminal=true/false` controls whether Terminal.app opens
- `refresh=true` tells SwiftBar to re-execute the plugin
- `~~~` (streamable mode) signals "end of menu, render now"
- Pipes in output text must be escaped (replaced with `/`)

**Edge cases handled:**
- No runs directory → shows "No runs found"
- Malformed JSON → silently skipped
- Missing fields → defaults used
- Long output → truncated with line count
- Pipes in output → escaped for SwiftBar compatibility
- tender binary location → searched in common paths

---

## Installation

### File: `menubar/install.sh`

```bash
#!/bin/bash
set -e

# --- system-tender menubar plugin installer ---

PLUGIN_NAME="system-tender-status.1m.py"
PLUGIN_SOURCE="$(cd "$(dirname "$0")" && pwd)/${PLUGIN_NAME}"

# Check for SwiftBar
if ! command -v swiftbar &>/dev/null && [ ! -d "/Applications/SwiftBar.app" ]; then
    echo "SwiftBar not found. Installing via Homebrew..."
    if ! command -v brew &>/dev/null; then
        echo "ERROR: Homebrew not found. Install SwiftBar manually:"
        echo "  brew install --cask swiftbar"
        echo "  OR download from: https://github.com/swiftbar/SwiftBar/releases"
        exit 1
    fi
    brew install --cask swiftbar
    echo "SwiftBar installed."
fi

# Find or create SwiftBar plugin directory
# SwiftBar stores its plugin directory in its preferences
SWIFTBAR_PLUGIN_DIR=""

# Check defaults
PREF_DIR=$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)
if [ -n "$PREF_DIR" ] && [ -d "$PREF_DIR" ]; then
    SWIFTBAR_PLUGIN_DIR="$PREF_DIR"
fi

# Fallback: common locations
if [ -z "$SWIFTBAR_PLUGIN_DIR" ]; then
    for dir in \
        "$HOME/Library/Application Support/SwiftBar/Plugins" \
        "$HOME/.swiftbar" \
        "$HOME/swiftbar-plugins"; do
        if [ -d "$dir" ]; then
            SWIFTBAR_PLUGIN_DIR="$dir"
            break
        fi
    done
fi

# If still not found, ask user or use default
if [ -z "$SWIFTBAR_PLUGIN_DIR" ]; then
    DEFAULT_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
    echo "SwiftBar plugin directory not found."
    echo "Using default: $DEFAULT_DIR"
    echo "If SwiftBar uses a different directory, set it in SwiftBar preferences."
    SWIFTBAR_PLUGIN_DIR="$DEFAULT_DIR"
    mkdir -p "$SWIFTBAR_PLUGIN_DIR"
fi

echo "Plugin directory: $SWIFTBAR_PLUGIN_DIR"

# Copy or symlink plugin
DEST="${SWIFTBAR_PLUGIN_DIR}/${PLUGIN_NAME}"

if [ -f "$PLUGIN_SOURCE" ]; then
    # Symlink so updates are automatic
    ln -sf "$PLUGIN_SOURCE" "$DEST"
    chmod +x "$PLUGIN_SOURCE"
    echo "Installed (symlinked): $DEST -> $PLUGIN_SOURCE"
else
    echo "ERROR: Plugin source not found: $PLUGIN_SOURCE"
    exit 1
fi

# Verify Python 3 available
if ! command -v python3 &>/dev/null; then
    echo "WARNING: python3 not found on PATH. Plugin requires Python 3."
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "Python: $PYTHON_VERSION"

# Verify runs directory exists
RUNS_DIR="$HOME/.config/system-tender/runs"
if [ -d "$RUNS_DIR" ]; then
    RUN_COUNT=$(ls "$RUNS_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
    echo "Runs directory: $RUNS_DIR ($RUN_COUNT runs found)"
else
    echo "WARNING: Runs directory not found: $RUNS_DIR"
    echo "Run 'tender init' to initialize system-tender."
fi

echo ""
echo "Installation complete."
echo "Open SwiftBar to see the system-tender status in your menubar."
echo ""
echo "To auto-start SwiftBar on login:"
echo "  1. Open System Settings > General > Login Items"
echo "  2. Add SwiftBar.app"
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Runs directory doesn't exist | Shows "No runs found", keeps polling |
| Malformed JSON file | Skipped silently, other runs still shown |
| tender binary not found | "Run Task" items still shown, will error in Terminal when clicked |
| SwiftBar not running | Plugin doesn't run (it's managed by SwiftBar) |
| Python crash | SwiftBar restarts plugin automatically (streamable mode) |
| Very large output in run | Truncated to MAX_OUTPUT_LINES (20) |
| Pipes in output text | Replaced with `/` to prevent SwiftBar parsing errors |

---

## Auto-Start on Login

SwiftBar handles this. Once SwiftBar is in Login Items:

1. **System Settings > General > Login Items > Add SwiftBar.app**
2. OR: `osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/SwiftBar.app", hidden:true}'`

SwiftBar will automatically load all plugins from its plugin directory on launch, including the system-tender plugin.

---

## Build and Packaging

**There is no build step.** The plugin is a single Python file using only stdlib.

To distribute:
1. Include `menubar/` directory in the system-tender repo
2. Users run `menubar/install.sh`
3. Done

Potential future enhancement: add `tender menubar install` CLI command that does the same thing.

---

## Testing the Plugin

```bash
# Run the plugin directly to verify output format
python3 menubar/system-tender-status.1m.py

# Should output SwiftBar-formatted text, then ~~~ separator
# Ctrl+C to stop the loop

# Verify SwiftBar picks it up
# 1. Open SwiftBar
# 2. Check menubar for "ST: X/Y"
# 3. Click to see the dropdown
```

---

## Future Enhancement Path

If a standalone .app is ever needed (no SwiftBar dependency):

1. **rumps + watchdog** — Python framework for macOS menubar apps
   - `pip install rumps watchdog`
   - Same data reading logic
   - Package with py2app: `python setup.py py2app`
   - Creates `system-tender-status.app` bundle
   - Set `LSUIElement: True` to hide dock icon

2. **Native Swift** — Full native app
   - Best UX and stability
   - Requires Xcode and Swift knowledge
   - Use MenuBarExtra (SwiftUI) or NSStatusItem (AppKit)
   - FSEvents for file watching

These are upgrade paths, not requirements. The SwiftBar plugin covers all stated needs.

---

## References

- [SwiftBar GitHub](https://github.com/swiftbar/SwiftBar) — Plugin format, streamable mode docs
- [SwiftBar Plugin API](https://github.com/swiftbar/SwiftBar/blob/main/README.md) — Full parameter reference
- [rumps](https://github.com/jaredks/rumps) — Python menubar framework (backup option)
- [Apple SF Symbols](https://developer.apple.com/sf-symbols/) — Icon reference for sfimage= parameter
