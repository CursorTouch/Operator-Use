# Browser automation

Operator controls Chrome and Edge browsers via the Chrome DevTools Protocol (CDP). The `browser` tool provides a complete action set for navigation, interaction, content extraction, and tab management.

## Architecture

```
operator_use/browser/
  client/
    service.py      ← Browser class — top-level facade
    session.py      ← Session management
    page.py         ← Page actions (click, type, scroll, etc.)
    views.py        ← BrowserState, BrowserConfig, Tab
    config.py       ← BrowserConfig defaults
    events.py       ← Browser event types
  dom/
    dom.py          ← DOM helpers (element lookup, highlight, etc.)
  watchdog/
    base.py         ← crash/disconnect recovery
    crash.py        ← Crash detector
    dialog.py       ← Dialog auto-dismissal
    download.py     ← Download tracker
    popup.py        ← Popup handler
    state.py        ← StateInvalidatedEvent emission
  cdp/              ← Generated CDP protocol bindings
```

The `Browser` class is a high-level async facade over the CDP client. It tracks sessions, tabs, and emits typed events (`NavigationStartedEvent`, `NavigationSettledEvent`, `PopupOpenedEvent`, `StateInvalidatedEvent`).

## Tool actions

| Action | Required fields | Description |
|---|---|---|
| `open` | — | Launch or attach to a browser |
| `close` | — | Shut down the browser session |
| `goto` | `url` | Navigate to a URL |
| `back` | — | Go back in browser history |
| `forward` | — | Go forward in browser history |
| `click` | `x`, `y` | Click at screen coordinates |
| `type` | `x`, `y`, `text` | Click and type text |
| `key` | `text` | Press a key or combo (`Enter`, `Ctrl+a`, etc.) |
| `scroll` | `x`, `y` | Scroll up or down |
| `menu` | `x`, `y`, `labels` | Select options from a dropdown |
| `upload` | `x`, `y`, `filenames` | Upload files from `./uploads/` |
| `tab` | — | Open, close, or switch browser tabs |
| `wait` | `time` | Wait for a fixed duration (seconds) |
| `script` | `script` | Execute JavaScript and return the result |
| `scrape` | — | Extract the current page's readable text/DOM |
| `download` | `url`, `filename` | Download a file |

### `open` options

| Field | Default | Description |
|---|---|---|
| `browser` | `"chrome"` | `"chrome"` or `"edge"` |
| `headless` | `false` | Launch in headless mode |
| `attach_to_existing` | `false` | Attach to an already-running CDP session |
| `cdp_port` | `9222` | CDP debug port (when `attach_to_existing=true`) |

### `type` options

| Field | Default | Description |
|---|---|---|
| `clear` | `false` | Clear the field before typing |
| `press_enter` | `false` | Press Enter after typing |

### `key` options

| Field | Default | Description |
|---|---|---|
| `times` | `1` | Number of key presses (max 50) |

### `scroll` options

| Field | Default | Description |
|---|---|---|
| `direction` | `"down"` | `"up"` or `"down"` |
| `amount` | `500` | Pixels to scroll |

### `tab` options

| Field | Default | Description |
|---|---|---|
| `tab_mode` | `"open"` | `"open"`, `"close"`, or `"switch"` |
| `tab_index` | — | Zero-based index for `tab_mode="switch"` |

## Lifecycle

The browser tool enforces an explicit open/close lifecycle. Calling any action before `open` returns an error. Only one browser session can be active at a time per agent.

```python
# 1 — open
{ "action": "open", "browser": "chrome" }

# 2 — interact
{ "action": "goto", "url": "https://example.com" }
{ "action": "scrape" }

# 3 — close (required before switching browser type)
{ "action": "close" }
```

## Ephemeral state injection

At the start of each LLM turn, the browser tool injects a compact state message into the context:

```
[Browser: chrome | https://example.com | Example Domain | 3 tab(s)]
```

This message is **never written to session history**. It is rebuilt from the live browser state each turn and removed after the turn ends. The model always sees the current URL, page title, and tab count without a separate tool call.

## Watchdog

The browser client includes automatic recovery helpers:

- **Crash detector** — detects CDP disconnects and emits `StateInvalidatedEvent`
- **Dialog dismissal** — automatically handles JavaScript dialogs (alert, confirm, prompt)
- **Popup handler** — captures popups opened in new windows
- **Download tracker** — tracks in-progress and completed downloads

## DOM helpers

The `dom` module provides element lookup by CSS selector or XPath and coordinate resolution, used internally by `click`, `type`, `scroll`, and related actions.

## Settings

```json
{ "browser_use_enabled": true }
```

When `browser_use_enabled` is `false` (the default), the `browser` tool is hidden from the LLM tool list. Enable it in `settings.json` or via the `control_center` tool:

```python
{ "action": "set", "key": "browser_use_enabled", "value": true }
```

## Related documents

- [docs/computer.md](./computer.md) — Desktop computer control
- [docs/tool.md](./tool.md) — Tool interface and execution modes
- [docs/agent.md](./agent.md) — Ephemeral state injection into the LLM context
