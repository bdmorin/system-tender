```
 _____ __ __ _____ _____ _____ _____      _____ _____ _____ ____  _____ _____
|   __|  |  |   __|_   _|   __|     |    |_   _|   __|   | |    \|   __|   _ |
|__   |_   _|__   | | | |   __| | | |_____ | | |   __| | | |  |  |   __|    _|
|_____| |_| |_____| |_| |_____|_|_|_|_____||_| |_____|_|___|____/|_____|__|__|
```

---

<pre>
SYSTEM-TENDER v0.1.0
Like a tender on a locomotive -- keeps the engine running.
</pre>

---

system-tender is a CLI tool that hands system maintenance tasks to Claude and
lets it execute them. You write a TOML file describing what needs doing. Claude
reads it, gets a set of tools (shell, file I/O, HTTP, notifications), and does
the work in an agentic loop. It is a smart cron. It runs your tasks on a
schedule or on demand, saves structured run history, and generates native
scheduler configs for launchd, systemd, and cron. It does not have a GUI. It
does not have a web interface. It is a command-line tool that talks to the
Anthropic API.

---

## // ARCHITECTURE

<pre>
+------------------------------------------------------------------+
|                         TASK TOML FILE                           |
|  name, prompt, allowed_tools, timeout, schedule, network policy  |
+--------------------------------+---------------------------------+
                                 |
                                 v
+------------------------------------------------------------------+
|                         CLI (click)                               |
|  tender run | tender list | tender init | tender history         |
|  tender generate-schedule                                        |
+--------------------------------+---------------------------------+
                                 |
                                 v
+------------------------------------------------------------------+
|                       AGENTIC ENGINE                             |
|                                                                  |
|   +-----------+     +------------------+     +----------------+  |
|   |  Anthropic | --> |  Tool Dispatch   | --> |  Tool Execute  |  |
|   |  Messages  |     |  (per-tool ACL)  |     |                |  |
|   |  API Loop  | <-- |  Network Policy  | <-- |  shell_execute |  |
|   +-----------+     +------------------+     |  file_read     |  |
|                                              |  file_write    |  |
|                                              |  http_request  |  |
|                                              |  notify        |  |
|                                              +----------------+  |
+--------------------------------+---------------------------------+
                                 |
                                 v
+------------------------------------------------------------------+
|                       RUN HISTORY                                |
|  ~/.config/system-tender/runs/YYYYMMDD-HHMMSS-task-runid.json   |
|  Permissions: 0600 | Secrets redacted from stored tool calls    |
+------------------------------------------------------------------+
</pre>

---

## // INSTALLATION

Requires Python 3.12+ and an Anthropic API key.

```sh
# uv (recommended)
uv pip install .

# pip
pip install .

# development
uv pip install -e ".[dev]"
```

The install creates two CLI entry points: `system-tender` and `tender` (alias).

---

## // QUICK START

```sh
# 1. Initialize config directory
tender init

# 2. Set your API key
echo 'ANTHROPIC_API_KEY=sk-ant-...' > ~/.config/system-tender/.env
chmod 600 ~/.config/system-tender/.env

# 3. Run the example task
tender run system-check

# 4. Run an ad-hoc prompt
tender run --prompt "Check disk usage and report anything over 80%"

# 5. See what happened
tender history
```

`tender init` creates:

```
~/.config/system-tender/
    config.toml          # global settings
    tasks/               # task TOML files
        system-check.toml
    logs/                # rotating log files
    runs/                # JSON run history
```

> WARNING: The config directory is created with 0700 permissions. The .env file
> should be 0600. system-tender will warn if your .env is world-readable.

---

## // CONFIGURATION

### Global Config: `config.toml`

```toml
[tender]
model = "claude-sonnet-4-6"
max_tokens = 4096
default_timeout = 300
```

| Field             | Type   | Default              | Description                     |
|-------------------|--------|----------------------|---------------------------------|
| `model`           | string | `claude-sonnet-4-6`  | Anthropic model ID              |
| `max_tokens`      | int    | `4096`               | Max response tokens per API call|
| `default_timeout` | int    | `300`                | Default task timeout (seconds)  |

---

### Task Config: TOML Format

Tasks live in `~/.config/system-tender/tasks/` as individual `.toml` files.

```toml
[task]
name = "brew-update"
description = "Update homebrew packages and report what changed"
allowed_tools = ["shell", "file_read"]
timeout = 300
network_access = false
egress_allowlist = []
# model = "claude-sonnet-4-6"       # override global model
# system_prompt = "..."             # override default system prompt

[task.prompt]
text = """
Update all homebrew packages. Steps:
1. Run `brew update` to refresh the package index
2. Run `brew outdated` to see what needs updating
3. Run `brew upgrade` to update all packages
4. Report: what was updated, what failed, and any caveats
"""

[output]
format = "text"

[schedule]
cron = "0 6 * * *"

[env]
# KEY = "VALUE"  -- injected into task environment
```

#### All Task Fields

| Field              | Type       | Default                    | Description                                      |
|--------------------|------------|----------------------------|--------------------------------------------------|
| `name`             | string     | **required**               | Task identifier                                  |
| `description`      | string     | `""`                       | Human-readable description                       |
| `system_prompt`    | string     | global default             | Override the system prompt for this task          |
| `model`            | string     | global config              | Override the model for this task                  |
| `timeout`          | int        | `300`                      | Max execution time in seconds                    |
| `allowed_tools`    | list[str]  | `["shell", "file_read"]`   | Tools Claude can use (see Tools section)         |
| `prompt`           | string/obj | **required**               | The prompt text, or `{text, context_files}`      |
| `output_format`    | string     | `"text"`                   | `text` or `structured`                           |
| `schedule`         | string     | none                       | 5-field cron expression                          |
| `network_access`   | bool       | `false`                    | Enable/disable the http_request tool             |
| `egress_allowlist` | list[str]  | `[]` (all hosts if empty)  | Hostname patterns for http_request (fnmatch)     |
| `env`              | table      | `{}`                       | Environment variables for the task               |

### Environment: `.env`

Place at `~/.config/system-tender/.env`. Only variables prefixed with `ANTHROPIC_`
are loaded. Everything else is rejected.

```sh
ANTHROPIC_API_KEY=sk-ant-api03-...
```

> WARNING: Non-ANTHROPIC_ prefixed variables are silently dropped. This is
> intentional -- prevents hijacking PATH, LD_PRELOAD, or PYTHONPATH.

---

## // CLI REFERENCE

```
Usage: tender [OPTIONS] COMMAND [ARGS]...

Options:
  --version              Show version
  --config-dir PATH      Override config directory
  -v, --verbose          Enable debug logging
  --help                 Show this message and exit
```

---

### `tender run`

Run a maintenance task.

```
Usage: tender run [OPTIONS] [TASK_NAME]

Options:
  -p, --prompt TEXT        Ad-hoc prompt (no task file needed)
  -f, --task-file PATH     Run from a specific task file
  -m, --model TEXT         Override model
  -t, --timeout INTEGER    Override timeout (seconds)
  --json-output            Output full JSON result
  --help                   Show this message and exit
```

```sh
# Run a named task
tender run brew-update

# Run with ad-hoc prompt
tender run --prompt "List all listening TCP ports"

# Run from a file outside the config dir
tender run --task-file ./my-custom-task.toml

# Override model for one run
tender run disk-report --model claude-haiku-4-5-20251001

# Get structured JSON output
tender run system-check --json-output
```

---

### `tender list`

List all configured tasks.

```sh
tender list
```

Output:

```
  brew-update          Update homebrew packages and report w...  [shell, file_read] (0 6 * * *)
  disk-report          Check disk usage and flag anything ov...  [shell] (0 8 * * 1)
```

---

### `tender init`

Initialize the config directory with defaults and an example task.

```sh
tender init
```

```
Initialized: /Users/you/.config/system-tender
  Config:  /Users/you/.config/system-tender/config.toml
  Tasks:   /Users/you/.config/system-tender/tasks/
  Logs:    /Users/you/.config/system-tender/logs/
  Runs:    /Users/you/.config/system-tender/runs/
```

---

### `tender history`

Show recent run history.

```
Usage: tender history [OPTIONS]

Options:
  -n, --last INTEGER    Number of recent runs (default: 10)
```

```sh
tender history
tender history --last 5
```

```
  [OK]   brew-update          a1b2c3d4e5f6  12.3s  1847 tokens  20260227-060012
  [FAIL] disk-report          f6e5d4c3b2a1   3.1s   423 tokens  20260227-080005
```

---

### `tender generate-schedule`

Generate native scheduler configs for a task.

```
Usage: tender generate-schedule [OPTIONS] TASK_NAME

Options:
  --type [launchd|systemd|cron|auto]   Scheduler type (default: auto)
  -s, --schedule TEXT                  Override cron schedule
  --install                            Install the schedule (launchd only)
```

```sh
# Auto-detect platform and print config
tender generate-schedule brew-update

# Generate launchd plist
tender generate-schedule brew-update --type launchd

# Generate and install launchd plist
tender generate-schedule brew-update --type launchd --install

# Generate systemd units
tender generate-schedule brew-update --type systemd

# Generate crontab entry with custom schedule
tender generate-schedule brew-update --type cron --schedule "0 */4 * * *"
```

---

## // TOOLS

Claude gets access to tools based on the `allowed_tools` list in each task config.
Default: `["shell", "file_read"]`. Available tools:

---

### `shell_execute`

Execute a shell command and return stdout/stderr.

```json
{
  "name": "shell_execute",
  "input_schema": {
    "type": "object",
    "properties": {
      "command":     { "type": "string",  "description": "The shell command to execute" },
      "timeout":     { "type": "integer", "description": "Timeout in seconds (default: 60, max: 3600)" },
      "working_dir": { "type": "string",  "description": "Working directory (optional)" }
    },
    "required": ["command"]
  }
}
```

> NOTE: Commands run with `shell=True`. Timeout is hard-capped at 3600 seconds.

---

### `file_read`

Read the contents of a file.

```json
{
  "name": "file_read",
  "input_schema": {
    "type": "object",
    "properties": {
      "path":      { "type": "string",  "description": "Absolute path to the file" },
      "max_bytes": { "type": "integer", "description": "Max bytes to read (default: 1048576)" }
    },
    "required": ["path"]
  }
}
```

---

### `file_write`

Write content to a file. Creates parent directories if needed.

```json
{
  "name": "file_write",
  "input_schema": {
    "type": "object",
    "properties": {
      "path":    { "type": "string",  "description": "Absolute path to write to" },
      "content": { "type": "string",  "description": "Content to write" },
      "append":  { "type": "boolean", "description": "Append instead of overwrite (default: false)" }
    },
    "required": ["path", "content"]
  }
}
```

> WARNING: No path restrictions. Only enable file_write in tasks that need it.
> Never run system-tender as root.

---

### `http_request`

Make an HTTP request. Gated by network access policy.

```json
{
  "name": "http_request",
  "input_schema": {
    "type": "object",
    "properties": {
      "url":     { "type": "string", "description": "The URL to request" },
      "method":  { "type": "string", "enum": ["GET","POST","PUT","DELETE","PATCH"] },
      "headers": { "type": "object", "description": "HTTP headers as key-value pairs" },
      "body":    { "type": "string", "description": "Request body (for POST/PUT/PATCH)" }
    },
    "required": ["url"]
  }
}
```

---

### `notify`

Send a native system notification (macOS Notification Center / Linux notify-send).

```json
{
  "name": "notify",
  "input_schema": {
    "type": "object",
    "properties": {
      "title":   { "type": "string",  "description": "Notification title" },
      "message": { "type": "string",  "description": "Notification body text" },
      "sound":   { "type": "boolean", "description": "Play sound (default: true, macOS only)" }
    },
    "required": ["title", "message"]
  }
}
```

---

## // NETWORK ACCESS CONTROL

HTTP requests are denied by default. Two fields control network access per task:

| Field              | Type       | Default | Effect                                      |
|--------------------|------------|---------|---------------------------------------------|
| `network_access`   | bool       | `false` | Must be `true` for http_request to work     |
| `egress_allowlist` | list[str]  | `[]`    | If non-empty, only these hosts are allowed  |

The allowlist uses `fnmatch` glob matching against the URL hostname.

```toml
[task]
network_access = true
egress_allowlist = ["hooks.slack.com", "*.github.com"]
```

**What this covers**: The `http_request` tool only.

**What this does NOT cover**: Shell commands. `curl`, `wget`, `brew update`, and
any other subprocess have unrestricted network access. True network isolation
requires OS-level sandboxing (`sandbox-exec` on macOS, namespaces on Linux).

---

## // SCHEDULER INTEGRATION

system-tender generates native scheduler configurations from the `schedule` field
in task TOML files. The schedule is a standard 5-field cron expression.

```
# .---------------- minute (0-59)
# |  .------------- hour (0-23)
# |  |  .---------- day of month (1-31)
# |  |  |  .------- month (1-12)
# |  |  |  |  .---- day of week (0-7, 0 and 7 = Sunday)
# |  |  |  |  |
  0  6  *  *  *     # daily at 06:00
  0  8  *  *  1     # every Monday at 08:00
  0  3  *  *  0     # every Sunday at 03:00
```

---

### macOS launchd

```sh
tender generate-schedule brew-update --type launchd --install
```

Generates a plist at `~/Library/LaunchAgents/com.system-tender.brew-update.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.system-tender.brew-update</string>
    <key>ProgramArguments</key>
    <array>
      <string>tender</string>
      <string>run</string>
      <string>brew-update</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
      <key>Hour</key>
      <integer>6</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>~/.config/system-tender/logs/brew-update.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>~/.config/system-tender/logs/brew-update.stderr.log</string>
  </dict>
</plist>
```

```sh
launchctl load ~/Library/LaunchAgents/com.system-tender.brew-update.plist
```

> NOTE: launchd does not support step values (*/5). Use explicit values or crontab.

---

### Linux systemd

```sh
tender generate-schedule brew-update --type systemd
```

Produces two units:

**Service** (`system-tender-brew-update.service`):

```ini
[Unit]
Description=system-tender task: brew-update

[Service]
Type=oneshot
ExecStart=tender run brew-update

[Install]
WantedBy=multi-user.target
```

**Timer** (`system-tender-brew-update.timer`):

```ini
[Unit]
Description=Timer for system-tender task: brew-update

[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
# Install (user units)
cp system-tender-brew-update.service ~/.config/systemd/user/
cp system-tender-brew-update.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now system-tender-brew-update.timer
```

---

### crontab

```sh
tender generate-schedule brew-update --type cron
```

```
0 6 * * * tender run brew-update
```

Add to your crontab with `crontab -e`.

---

## // SECURITY MODEL

system-tender was reviewed for security issues. Four HIGH findings were
identified and fixed. See `docs/security-review.md` for the full report.

### Trust Boundaries

```
+-------------------+     +---------------------+     +------------------+
|   User (config)   | --> |  Claude (API calls) | --> |  Local execution |
|                   |     |                     |     |                  |
| - Authors TOML    |     | - Decides tool use  |     | - Runs as user   |
| - Sets tool ACLs  |     | - Within allowed    |     | - No sandboxing  |
| - Defines prompts |     |   tool set only     |     | - Full user privs|
+-------------------+     +---------------------+     +------------------+
```

### Mitigations In Place

| Threat                            | Mitigation                                                |
|-----------------------------------|-----------------------------------------------------------|
| Env var injection via .env        | Only `ANTHROPIC_*` prefixed vars loaded                   |
| Secrets in run history            | Auth headers redacted before storage                      |
| Permissive file permissions       | Config dir: 0700, run files: 0600                         |
| Overly permissive .env            | Warning logged if group/other readable                    |
| Unbounded shell timeout           | Hard cap at 3600 seconds                                  |
| Unrestricted HTTP requests        | `network_access` default false, `egress_allowlist`        |
| Tool misuse by Claude             | Per-task `allowed_tools` whitelist                        |

### Known Limitations

- `file_write` has no path restrictions. Only enable it in tasks that need it.
- Shell commands (`curl`, `wget`) bypass the egress allowlist.
- DNS rebinding can circumvent hostname-based egress filtering.
- Shell commands logged at INFO may contain embedded secrets.
- `system_prompt` in task TOML is fully user-controlled. Review imported configs.
- Never run as root.

---

## // CREATING TASKS

### Step 1: Write the TOML

Create `~/.config/system-tender/tasks/disk-report.toml`:

```toml
[task]
name = "disk-report"
description = "Check disk usage and flag anything over 80%"
allowed_tools = ["shell"]
timeout = 60

[task.prompt]
text = """
Check disk usage on all mounted volumes. Report:
1. Each volume's usage percentage
2. Flag any volume over 80% usage as WARNING
3. Flag any volume over 95% usage as CRITICAL
4. List the top 5 largest directories in the home folder
"""

[output]
format = "text"

[schedule]
cron = "0 8 * * 1"
```

### Step 2: Verify It Loads

```sh
tender list
```

```
  disk-report          Check disk usage and flag anything ov...  [shell] (0 8 * * 1)
```

### Step 3: Run It

```sh
tender run disk-report
```

```
[OK] disk-report (a1b2c3d4e5f6)
  Duration: 8.2s | 1423 tokens | 3 tool calls

Disk Usage Report:
  /          : 62% [OK]
  /System/V  : 62% [OK]

Top 5 directories in ~/:
  1. ~/Library    12.3 GB
  2. ~/cowork      4.1 GB
  ...
```

### Step 4: Check History

```sh
tender history --last 1
```

### Step 5: Schedule It

```sh
tender generate-schedule disk-report --type launchd --install
launchctl load ~/Library/LaunchAgents/com.system-tender.disk-report.plist
```

---

### More Examples

<details>
<summary><code>brew-update.toml</code> -- Update homebrew packages daily at 6am</summary>

```toml
[task]
name = "brew-update"
description = "Update homebrew packages and report what changed"
allowed_tools = ["shell", "file_read"]
timeout = 300

[task.prompt]
text = """
Update all homebrew packages. Steps:
1. Run `brew update` to refresh the package index
2. Run `brew outdated` to see what needs updating
3. Run `brew upgrade` to update all packages
4. Report: what was updated, what failed, and any caveats or warnings
"""

[output]
format = "text"

[schedule]
cron = "0 6 * * *"
```
</details>

<details>
<summary><code>webhook-callback.toml</code> -- Post results to Slack (restricted egress)</summary>

```toml
[task]
name = "webhook-callback"
description = "Send task results to Slack via webhook"
allowed_tools = ["shell", "file_read", "http_request"]
timeout = 120
network_access = true
egress_allowlist = ["hooks.slack.com"]

[task.prompt]
text = """
1. Read the latest run result from ~/.config/system-tender/runs/
2. Summarize the result as a Slack message payload
3. POST it to the Slack webhook URL in $SLACK_WEBHOOK_URL
"""

[env]
SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/REPLACE/WITH/REAL"

[output]
format = "text"
```
</details>

<details>
<summary><code>uptime-notify.toml</code> -- Desktop notification with system uptime</summary>

```toml
[task]
name = "uptime-notify"
description = "Check system uptime and send a desktop notification"
allowed_tools = ["shell", "notify"]
timeout = 60

[task.prompt]
text = """
1. Run 'uptime' to check how long the system has been running.
2. Send a notification with the title "System Uptime" and the uptime as the message.
"""

[output]
format = "text"
```
</details>

---

## // RUN HISTORY

Every task execution is saved as a JSON file in `~/.config/system-tender/runs/`.

**Filename format**: `YYYYMMDD-HHMMSS-taskname-runid.json`

**Permissions**: `0600` (owner read/write only)

```json
{
  "run_id": "a1b2c3d4e5f6",
  "task_name": "brew-update",
  "started_at": "2026-02-27T06:00:12.345678+00:00",
  "completed_at": "2026-02-27T06:00:24.567890+00:00",
  "success": true,
  "output": "Updated 3 packages...",
  "error": null,
  "tool_calls": [
    {
      "tool_name": "shell_execute",
      "input": { "command": "brew update" },
      "output": "stdout:\nAlready up-to-date...",
      "duration_ms": 2341,
      "success": true
    }
  ],
  "input_tokens": 1234,
  "output_tokens": 567,
  "model": "claude-sonnet-4-6",
  "duration_ms": 12234
}
```

> NOTE: HTTP Authorization, X-Api-Key, Cookie, and other sensitive headers are
> redacted to `[REDACTED]` before storage. Shell commands containing embedded
> secrets are NOT redacted -- they are opaque strings.

---

## // PROJECT STRUCTURE

```
system-tender/
    pyproject.toml
    src/system_tender/
        __init__.py              # version
        cli.py                   # click CLI (run, list, init, history, generate-schedule)
        config.py                # TOML loading, config dir init
        engine.py                # agentic loop, tool definitions, tool dispatch
        logger.py                # cross-platform logging (syslog, journald, file, console)
        models.py                # pydantic models (TaskConfig, GlobalConfig, TaskResult)
        scheduler.py             # launchd/systemd/cron generation
    tests/
        conftest.py
        test_cli.py
        test_config.py
        test_engine.py
        test_models.py
        test_scheduler.py
    examples/
        config.toml              # example global config
        tasks/
            brew-update.toml
            brew-update-restricted.toml
            disk-report.toml
            git-maintenance.toml
            notify-example.toml
            webhook-callback.toml
    docs/
        security-review.md
        plans/
        research/
```

---

## // LICENSE

MIT License

Copyright (c) 2026 Brian Morin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

<pre>
                                         ___
                                     ___/   \___
                                    /   '---'   \
                                   /  |       |  \
                                  |   |  [S]  |   |
                                  |   |  [T]  |   |
                                  |   |       |   |
                                   \__|_______|__/
                                      |       |
                                      |  |||  |
                                      |  |||  |
                                  ====|  |||  |====
                                      |_______|
</pre>
