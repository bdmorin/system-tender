# system-tender Security Review

**Date**: 2026-02-27
**Scope**: Full source review of `src/system_tender/` (engine.py, config.py, models.py, cli.py, logger.py, scheduler.py)
**Version**: 0.1.0

---

## Threat Model

system-tender is a CLI tool that delegates system maintenance tasks to Claude via the Anthropic API. The user authors TOML task configs defining what Claude can do. Claude then executes tools (shell commands, file I/O, HTTP requests, notifications) in a tool-use loop.

**Trust boundaries:**
- The user authors task configs and controls which tools are enabled per task
- Claude (via API) decides which tool calls to make within the allowed set
- Tool execution happens locally with the privileges of the running user
- Run history and logs are persisted to `~/.config/system-tender/`

**Primary threat actors:**
1. A confused/prompt-injected Claude making unintended tool calls
2. A malicious TOML config (supply chain — someone shares a bad config)
3. Local privilege escalation via secrets exposed in run history/logs

---

## Findings Summary

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | HIGH | Unrestricted .env variable injection | **FIXED** |
| 2 | HIGH | Run history stores secrets in plaintext | **FIXED** |
| 3 | HIGH | Run history files created with default (permissive) permissions | **FIXED** |
| 4 | HIGH | Config directory created with default permissions | **FIXED** |
| 5 | MEDIUM | .env file permission not validated | **FIXED** (warning) |
| 6 | MEDIUM | Shell timeout can be set arbitrarily by Claude | **FIXED** |
| 7 | MEDIUM | file_write tool has no path restrictions | Documented |
| 8 | MEDIUM | HTTP request tool — SSRF potential | Partially mitigated |
| 9 | LOW | Shell commands logged at INFO level may contain secrets | Documented |
| 10 | LOW | TOML config allows arbitrary system_prompt override | By design |
| 11 | INFO | `shell=True` in subprocess.run | By design |
| 12 | INFO | Dependencies are all well-known packages | No action needed |

---

## Detailed Findings

### 1. [HIGH] Unrestricted .env Variable Injection — FIXED

**File**: `engine.py:_load_env()`

**Description**: The `.env` loader parsed all `KEY=VALUE` lines and injected them into `os.environ` without restriction. A malicious or misconfigured `.env` file could overwrite critical environment variables like `PATH`, `LD_PRELOAD`, `PYTHONPATH`, or `HOME`.

**Impact**: If an attacker gains write access to `~/.config/system-tender/.env`, they could hijack the process environment to execute arbitrary code (e.g., via `LD_PRELOAD` or `PATH` manipulation).

**Fix**: Added an allowlist (`_ENV_ALLOWED_PREFIXES = ("ANTHROPIC_",)`) so only `ANTHROPIC_*` variables are loaded from the `.env` file. Non-matching variables now emit a warning and are skipped.

**Test**: `TestLoadEnvAllowlist` — verifies `PATH` is not overwritten, `ANTHROPIC_*` vars are loaded, and early-exit when key is already set.

---

### 2. [HIGH] Run History Stores Secrets in Plaintext — FIXED

**File**: `engine.py:run_task()` (ToolCall storage)

**Description**: Tool call inputs were stored verbatim in the `TaskResult.tool_calls` list, which is serialized to JSON in run history files. HTTP request headers (including `Authorization`, `X-Api-Key`, `Cookie`) would be persisted in plaintext.

**Impact**: API keys, auth tokens, and session cookies used in HTTP tool calls would be written to disk in `~/.config/system-tender/runs/*.json`, accessible to any process running as the same user (or broader, if permissions are wrong).

**Fix**: Added `_redact_tool_input()` which replaces values of sensitive HTTP headers with `[REDACTED]` before storing in run history. Sensitive headers: `authorization`, `x-api-key`, `cookie`, `set-cookie`, `proxy-authorization`, `x-auth-token`.

**Limitation**: Shell commands containing secrets (e.g., `curl -H "Authorization: ..."`) are NOT redacted because the command is an opaque string. This is documented as a known limitation.

**Test**: `TestRedactToolInput` — verifies header redaction, non-mutation, and passthrough for non-HTTP tools.

---

### 3. [HIGH] Run History Files Created with Default Permissions — FIXED

**File**: `engine.py:save_run()`

**Description**: Run history JSON files were created with default permissions (typically 0644 on macOS), making them readable by all users on the system. These files contain tool call outputs, which may include system information, file contents, or command outputs.

**Impact**: Other users on a shared system could read run history files containing sensitive system information.

**Fix**: Added `os.chmod(path, 0o600)` after writing run history files, restricting access to the file owner only.

**Test**: `TestRunFilePermissions` — verifies saved run files have 0600 permissions.

---

### 4. [HIGH] Config Directory Created with Default Permissions — FIXED

**File**: `config.py:init_config_dir()`

**Description**: The config directory (`~/.config/system-tender/`) was created with default permissions, potentially allowing other users to read task configs, logs, and the `.env` file containing the API key.

**Impact**: On shared systems, other users could read the Anthropic API key, task configurations, and run history.

**Fix**: Added `os.chmod(config_dir, 0o700)` after directory creation, restricting access to the owner only.

---

### 5. [MEDIUM] .env File Permission Not Validated — FIXED (warning)

**File**: `engine.py:_load_env()`

**Description**: The `.env` file containing the Anthropic API key was read without checking its file permissions. On macOS, files are often created with 0644 (world-readable) by default.

**Impact**: Other users on a shared system could read the API key.

**Fix**: Added a permission check that logs a warning if the `.env` file has group or other permissions set (i.e., `mode & 0o077 != 0`). The warning recommends `chmod 600`.

**Recommendation**: Consider auto-fixing permissions in a future version, or refusing to load an overly permissive `.env` file.

---

### 6. [MEDIUM] Shell Timeout Can Be Set Arbitrarily — FIXED

**File**: `engine.py:execute_shell()`

**Description**: Claude could specify any timeout value via the tool input schema's `timeout` field. Setting it to an extremely large value (e.g., 999999 seconds) would effectively disable the timeout safety mechanism.

**Impact**: A confused or prompt-injected Claude could run long-running commands without a timeout safety net, consuming resources indefinitely.

**Fix**: Added `MAX_SHELL_TIMEOUT = 3600` (1 hour) hard cap. The timeout is now clamped to `[1, 3600]` regardless of what Claude requests.

**Test**: `TestShellTimeoutCap` — verifies clamping for huge, negative, and zero values.

---

### 7. [MEDIUM] file_write Tool Has No Path Restrictions — Documented

**File**: `engine.py:execute_file_write()`

**Description**: The file_write tool can write to any path accessible by the running user. Claude controls the path. There is no allowlist/denylist for write destinations.

**Impact**: A confused or prompt-injected Claude could write to sensitive locations:
- `~/.ssh/authorized_keys` — add SSH keys
- `~/.bashrc` / `~/.zshrc` — inject shell commands
- `~/.config/system-tender/tasks/*.toml` — modify its own task definitions
- `/etc/crontab` (if running as root, which you shouldn't be)

**Mitigation**:
- Only enable `file_write` in tasks that need it (`allowed_tools` in TOML config)
- Never run system-tender as root
- Consider adding a `write_allowlist` field to TaskConfig in a future version

---

### 8. [MEDIUM] HTTP Request Tool — SSRF Potential — Partially Mitigated

**File**: `engine.py:execute_http_request()`

**Description**: The HTTP request tool can make requests to any URL, including internal network addresses (`169.254.169.254` for cloud metadata, `localhost`, RFC 1918 ranges).

**Impact**: If system-tender runs on a cloud instance or server with access to internal services, Claude could be directed to access:
- Cloud metadata endpoints (AWS/GCP/Azure instance credentials)
- Internal APIs and services
- Localhost services

**Mitigation already in place**: The `network_access` and `egress_allowlist` fields on TaskConfig provide task-level URL filtering via `check_egress_allowed()`. Tasks default to `network_access = false`.

**Remaining risk**: The allowlist uses hostname matching only. It does not resolve DNS (a hostname could resolve to an internal IP), and it does not block by IP address. Shell commands (`curl`, `wget`) bypass this check entirely.

---

### 9. [LOW] Shell Commands Logged at INFO Level

**File**: `engine.py:execute_shell()` line 210

**Description**: Full shell commands are logged at INFO level via `logger.info("shell: %s", command)`. Similarly, HTTP URLs are logged. These could contain embedded secrets.

**Impact**: Log files in `~/.config/system-tender/logs/` and syslog could contain sensitive data if Claude constructs commands with embedded credentials.

**Recommendation**: Consider logging only the first N characters of commands at INFO, with full commands at DEBUG. For now, the restrictive config dir permissions (finding #4 fix) limit exposure.

---

### 10. [LOW] TOML Config Allows Arbitrary system_prompt Override

**File**: `models.py:TaskConfig.system_prompt`, `engine.py:build_system_prompt()`

**Description**: Task TOML files can set `system_prompt` to any string, completely replacing the default system prompt. A malicious shared config could instruct Claude to perform unexpected actions.

**Impact**: If a user imports a TOML task config from an untrusted source, the `system_prompt` field could instruct Claude to exfiltrate data, write malicious files, etc.

**Mitigation**: This is by design — the task author controls what Claude is told to do. Users should review imported configs before running them.

---

### 11. [INFO] `shell=True` in subprocess.run — By Design

**File**: `engine.py:execute_shell()`

**Description**: Shell commands execute with `shell=True`, enabling shell features (pipes, redirects, globbing).

**Assessment**: This is the core design of the tool — it's a system maintenance agent that runs shell commands. The commands come from Claude's tool-use responses, not directly from untrusted user input. The trust boundary is: the user trusts Claude (within the constraints of their task config) to run appropriate commands.

**No action needed.** The existing mitigations (allowed_tools, timeout cap, max iterations) are appropriate.

---

### 12. [INFO] Dependencies — No Supply Chain Concerns

**File**: `pyproject.toml`

All dependencies are well-known, actively maintained packages:
- `anthropic` — Official Anthropic SDK
- `tomli` / `tomli-w` — TOML parsing (tomli is stdlib in 3.11+)
- `click` — CLI framework
- `pydantic` — Data validation
- `rich` — Terminal formatting

No unusual, unmaintained, or suspicious packages detected.

---

## Changes Made

### engine.py
1. Added `_ENV_ALLOWED_PREFIXES` constant — env var loading allowlist
2. Modified `_load_env()` — only loads `ANTHROPIC_*` vars, warns on others, checks .env file permissions
3. Added `MAX_SHELL_TIMEOUT = 3600` — hard cap on shell command timeouts
4. Modified `execute_shell()` — clamps timeout to `[1, 3600]`
5. Added `_SENSITIVE_HEADER_KEYS` and `_redact_tool_input()` — redacts auth headers in stored tool calls
6. Modified `run_task()` — uses `_redact_tool_input()` before storing tool call inputs
7. Modified `save_run()` — sets 0600 permissions on run history JSON files

### config.py
1. Added `import os`
2. Modified `init_config_dir()` — sets 0700 on config directory after creation

### tests/test_engine.py
Added security test classes:
- `TestRedactToolInput` (5 tests) — header redaction, non-mutation, passthrough
- `TestShellTimeoutCap` (4 tests) — timeout clamping edge cases
- `TestLoadEnvAllowlist` (3 tests) — env var filtering, early exit
- `TestRunFilePermissions` (1 test) — file permission verification

**All 145 tests pass.**

---

## Recommendations for Future Work

1. **File write path restrictions**: Add a `write_allowlist` or `write_denylist` field to TaskConfig for path-based access control on the file_write tool.

2. **DNS-aware egress filtering**: Resolve hostnames before checking the egress allowlist, to prevent SSRF via DNS rebinding.

3. **Log redaction**: Add a log filter that redacts common secret patterns (API keys, bearer tokens) from log output.

4. **Config file signing**: For shared/distributed task configs, consider signing TOML files to verify authorship and prevent tampering.

5. **OS-level sandboxing**: For high-security deployments, wrap tool execution in macOS `sandbox-exec` or Linux seccomp/namespaces to enforce filesystem and network restrictions at the OS level.
