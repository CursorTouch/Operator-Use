# Teams

Teams coordinate multiple agent workers. Each member is a subagent spawned against a named profile; members communicate via persistent inbox mailboxes.

## Architecture

```
operator_use/team/
  types.py      ← TeamRecord, TeamMember, MailboxMessage, TeamStatus
  manager.py    ← TeamManager — CRUD, member management, mailbox routing
  mailbox.py    ← TeamMailbox — inbox read/write per agent
```

Team state is persisted at:

```
~/.operator/profiles/<name>/teams/
  <team-name>/
    team.json           ← team record (name, members, status)
    agents/
      <agent-id>/
        inbox/          ← pending messages
```

**Teams require an active profile.** Without `--profile`, `TeamManager` raises `RuntimeError` on any write operation.

## Tool actions

```python
# create
{ "action": "create", "team_name": "research", "description": "Parallel research workers" }

# spawn a member (starts a subagent using profile role)
{ "action": "spawn", "team_name": "research",
  "member_name": "Alice", "role": "researcher",
  "task": "research climate change impacts 2025" }

# send a message to a member's inbox
{ "action": "send", "team_name": "research",
  "agent_id": "<id>", "message": "focus on renewable energy" }

# read a member's inbox (clears it)
{ "action": "inbox", "team_name": "research", "agent_id": "<id>" }

# check team status
{ "action": "status", "team_name": "research" }

# dissolve (marks team as dissolved, does not stop running members)
{ "action": "dissolve", "team_name": "research" }

# list all teams
{ "action": "list" }
```

## TeamRecord

```python
@dataclass
class TeamRecord:
    name: str
    description: str
    created_at: float          # epoch seconds
    creator_id: str            # "root" or spawning subagent task_id
    members: list[TeamMember]
    status: TeamStatus         # "active" | "dissolved"
```

## TeamMember

```python
@dataclass
class TeamMember:
    agent_id: str              # subagent task_id
    name: str                  # human-readable name
    role: str                  # profile name used to spawn this member
    joined_at: float
    status: TeamMemberStatus   # "active" | "idle" | "stopped"
    model: str | None
```

## Mailbox

Each team member has an inbox stored as individual JSON files. `TeamMailbox.send()` appends a message; `receive()` atomically reads and clears all pending messages; `peek()` reads without clearing.

```python
@dataclass
class MailboxMessage:
    id: str
    type: str          # "message" | "result" | "note"
    sender_id: str
    content: str
    created_at: float
```

## Persistence

Team state is written atomically (via a `.tmp` file rename) to `teams/<name>/team.json`. Inbox messages are individual JSON files under `teams/<name>/agents/<agent_id>/inbox/`.

All teams are loaded from disk at `TeamManager.__init__`. Members that were `active` at shutdown have their status set to `"stopped"` on load (they need to be re-spawned).

## TeamManager API

| Method | Description |
|---|---|
| `create(name, description, creator_id)` | Create a new team |
| `dissolve(name)` | Mark team as dissolved |
| `get(name)` | Return `TeamRecord` or `None` |
| `list_teams()` | List all teams |
| `add_member(team_name, member)` | Add a member |
| `update_member_status(team_name, agent_id, status)` | Update member status |
| `get_member(team_name, agent_id)` | Find member by agent_id |
| `find_member_by_name(team_name, name)` | Find member by name (case-insensitive) |
| `send_message(team_name, to_agent_id, sender_id, type, content)` | Post to member inbox |
| `read_inbox(team_name, agent_id)` | Read and clear inbox |
| `peek_inbox(team_name, agent_id)` | Read without clearing |

## Coordinator pattern

A typical coordinator pattern:

```python
# coordinator agent creates the team and spawns workers
{ "action": "create", "team_name": "analysis", "description": "Data analysis team" }
{ "action": "spawn", "team_name": "analysis", "member_name": "DataBot",
  "role": "analyst", "task": "load and summarise the CSV files in /data" }

# after the subagent task completes, read its inbox for results
{ "action": "inbox", "team_name": "analysis", "agent_id": "<DataBot-task-id>" }
```

Workers can post results back by calling `send` with `type="result"`.

## Related documents

- [docs/profiles.md](./profiles.md) — Profile requirement for team persistence
- [docs/subagent.md](./acp.md) — Subagent spawning used by `spawn` action
- [docs/workflow.md](./workflow.md) — Workflow alternative for structured pipelines
