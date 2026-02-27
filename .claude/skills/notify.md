---
description: Native system notification tool for system-tender tasks
---

# Notification Tool

system-tender includes a `notify` tool that sends native desktop notifications using the OS notification system. No pip dependencies required.

## Platform Behavior

- **macOS**: Uses `osascript` to trigger Notification Center alerts. Supports an optional sound.
- **Linux**: Uses `notify-send` (requires `libnotify-bin` / `libnotify` package). Sound parameter is ignored.

## Tool Schema

| Parameter | Type    | Required | Default | Description                          |
|-----------|---------|----------|---------|--------------------------------------|
| title     | string  | yes      | —       | Notification title                   |
| message   | string  | yes      | —       | Notification body text               |
| sound     | boolean | no       | true    | Play sound (macOS only)              |

## Task Configuration

Add `"notify"` to `allowed_tools` in your task TOML:

```toml
[task]
name = "uptime-notify"
description = "Check uptime and notify"
allowed_tools = ["shell", "notify"]
timeout = 60

[task.prompt]
text = """
1. Run 'uptime' to get system uptime.
2. Send a notification with title "System Uptime" and the result as the message.
"""
```

## Prompt Tips

When writing task prompts that use notifications:
- Tell the agent to use the notify tool explicitly ("Send a notification with title X and message Y")
- Pair with `shell` so the agent can gather data before notifying
- Keep notification messages concise — desktop notifications truncate long text
