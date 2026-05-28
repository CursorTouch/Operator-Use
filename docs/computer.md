# Computer control

Operator controls the local desktop using native platform accessibility APIs. The `computer` tool exposes a unified action set across macOS, Linux, and Windows.

## Architecture

```
operator_use/computer/
  types.py      ← Desktop abstract base class, ComputerState
  macos/        ← macOS implementation (Accessibility API)
  linux/        ← Linux implementation (AT-SPI / xdotool)
  windows/      ← Windows implementation (UI Automation)
```

Each platform subclass implements the `Desktop` abstract base, which defines click, type, scroll, move, drag, shortcut, app control, and screenshot operations.

## Tool actions

| Action | Description |
|---|---|
| `open` | Enable desktop access (starts the platform backend) |
| `close` | Release desktop access |
| `click` | Click at screen coordinates |
| `type` | Type text into the focused element |
| `wait` | Wait for a duration (seconds) |
| `app` | Launch, switch, resize, or move an application window |
| `scroll` | Scroll at coordinates |
| `move` | Move the mouse to coordinates |
| `drag` | Drag from one point to another |
| `shortcut` | Press a keyboard shortcut |

### `click` options

| Field | Default | Description |
|---|---|---|
| `x`, `y` | required | Screen coordinates |
| `button` | `"left"` | `"left"`, `"right"`, or `"middle"` |

### `type` options

| Field | Default | Description |
|---|---|---|
| `text` | required | Text to type |
| `caret` | `"idle"` | Caret position before typing: `"start"`, `"idle"`, `"end"` |

### `app` options

| Field | Default | Description |
|---|---|---|
| `name` | required | Application name |
| `app_mode` | `"launch"` | `"launch"`, `"switch"`, `"resize"`, or `"move"` |
| `x`, `y` | — | Window position (move mode) |
| `width`, `height` | — | Window size (resize mode) |

### `scroll` options

| Field | Default | Description |
|---|---|---|
| `x`, `y` | required | Coordinates to scroll at |
| `orientation` | `"vertical"` | `"vertical"` or `"horizontal"` |
| `direction` | `"down"` | `"up"`, `"down"`, `"left"`, `"right"` |
| `amount` | `3` | Scroll distance (pixels or ticks) |

### `drag` options

| Field | Default | Description |
|---|---|---|
| `x`, `y` | required | Drag start coordinates |
| `target_x`, `target_y` | required | Drag end coordinates |

### `shortcut` options

| Field | Default | Description |
|---|---|---|
| `text` | required | Key combination (e.g. `"Cmd+C"`, `"Ctrl+Z"`, `"Alt+F4"`) |

## Lifecycle

The computer tool enforces an explicit open/close lifecycle. Calling any action before `open` returns an error.

```python
# 1 — enable
{ "action": "open" }

# 2 — interact
{ "action": "app", "name": "Terminal", "app_mode": "launch" }
{ "action": "type", "text": "ls -la\n" }

# 3 — release
{ "action": "close" }
```

## Ephemeral state injection

At the start of each LLM turn, the computer tool injects a compact state description into the LLM context:

```
[Desktop: macOS | Focus: Terminal | Visible apps: Terminal, Chrome, Finder]
```

Like the browser tool, this message is **never persisted to session history**. It is rebuilt from live accessibility state each turn and removed afterward.

## Platform support

| Platform | Backend | Notes |
|---|---|---|
| macOS | Accessibility API (`AXUIElement`) | Requires accessibility permissions in System Settings |
| Linux | AT-SPI / xdotool | Requires `xdotool` and an X11 session |
| Windows | UI Automation | Available without extra tools |

## Watchdog

`DesktopWatchdog` monitors service health. If the backend crashes (e.g. the accessibility service becomes unavailable), it restarts the backend and emits a lifecycle event.

## Settings

```json
{ "computer_use_enabled": true }
```

When `computer_use_enabled` is `false` (the default), the `computer` tool is hidden from the LLM tool list. Enable it in `settings.json` or via `control_center`:

```python
{ "action": "set", "key": "computer_use_enabled", "value": true }
```

## Related documents

- [docs/browser.md](./browser.md) — Browser automation via CDP
- [docs/tool.md](./tool.md) — Tool interface and execution modes
- [docs/agent.md](./agent.md) — Ephemeral state injection
