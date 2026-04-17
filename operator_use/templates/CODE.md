# CODE.md - Your Codebase

_You're not just running on code. You are the code. Know thyself._

This file is your self-awareness layer — where you live, how you work, and how to improve yourself. Keep it accurate. When you change something, write it down here.

## What You Are

You're an async Python agent running on a bus architecture. Messages come in from channels (Telegram, Discord, Slack), flow through a bus to you, you think and act, then push responses back out. Simple in concept. Powerful in practice.

## Structure

```
(codebase root)/
├── operator_use/
│   ├── agent/           — You. LLM loop, tool execution, sessions, context, skills
│   ├── bus/             — Async message queues (incoming / outgoing)
│   ├── gateway/         — Channel lifecycle + outgoing dispatch loop
│   │   └── channels/    — Telegram, Discord, Slack implementations
│   ├── providers/       — LLM, STT, TTS provider adapters (14+ LLMs)
│   ├── session/         — Conversation history, persisted as JSONL
│   ├── heartbeat/       — Periodic task runner (~30 min interval)
│   ├── crons/           — Cron job scheduler
│   ├── config/          — Pydantic config schema, loaded from .operator_use/config.json
│   ├── tools/           — Tool and ToolResult models
│   └── templates/       — Default workspace files (SOUL.md, AGENTS.md, etc.)
└── .operator_use/
    ├── config.json      — Your active configuration
    ├── sessions/        — Conversation history per channel+chat_id
    ├── crons.json       — Persisted cron jobs
    ├── restart.json     — Written before restart if there's work to continue after. Auto-deleted on next startup.
    └── workspace/       — Your memory, skills, and identity files
```

## Key Flows

**Incoming message:**
Channel (Telegram/Discord/Slack) → `channel.receive()` → `Bus.publish_incoming()` → `Agent.ainvoke()` → session load → LLM call → tool calls (loop) → final response

**Outgoing message:**
`Agent` → `Bus.publish_outgoing()` → `Gateway._dispatch_loop()` → `channel.send()`

**Streaming:**
LLM streams chunks → `StreamPhase.START` (create message) → `StreamPhase.CHUNK` (edit in place) → `StreamPhase.END` (finalize) → `StreamPhase.DONE` (send media)

**Heartbeat:**
`Heartbeat` fires every 30 min → calls `agent.process_direct()` with HEARTBEAT.md prompt → runs as isolated session

**Cron:**
`Cron` fires on schedule → if `deliver=True`, publishes `OutgoingMessage` directly to bus → channel sends it

**Restart with continuation:**
`restart(continue_with="...")` → saves `restart.json` (task + channel + chat_id) → agent loop halts immediately (no further tool calls) → session saved to JSONL → 5s later `os._exit(75)` → supervisor relaunches → on startup runner finds `restart.json`, deletes it, waits 10s for gateway to connect, then publishes task back to original `channel:chat_id` via bus → agent loads the existing session (full history intact) → continues the task → sends final response back to user

## Conventions

- **Sessions** — Persisted at `.operator_use/sessions/{channel}_{chat_id}.jsonl`. One file per conversation.
- **Config** — `.operator_use/config.json`, merged with `OPERATOR_` env vars. Pydantic-validated.
- **New channel** — Inherit `BaseChannel`, implement `start()`, `stop()`, `_listen()`, `send()`. Register in `runner.py`.
- **New tool** — Add to `agent/tools/builtin/`, register in `ToolRegistry`.
- **New LLM provider** — Inherit `BaseChatLLM`, add to `providers/`, wire in `LLM_CLASS_MAP` in `runner.py`.

## Self-Improvement

You can read and modify your own codebase. The path is injected into your system prompt every session. Use it.

- Read files to understand how something works before changing it.
- Update CODE.md whenever you change the architecture, add a module, or adjust a flow.
- If you add a new skill, document it in `workspace/skills/{name}/SKILL.md`.
- If you fix a bug in yourself, note it in your daily memory file so you don't repeat the mistake.

The goal: stay self-aware. An agent that doesn't know how it works can't improve itself.

---
