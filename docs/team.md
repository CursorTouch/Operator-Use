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

## Practical Examples

### Example 1: Parallel Research Team

**Setup:** Multiple researchers working on different aspects of a topic.

```python
# In a session, create and coordinate a research team
{
  "action": "create",
  "team_name": "research-team",
  "description": "Parallel research on climate policy"
}

# Spawn team members (each uses the 'researcher' profile)
{
  "action": "spawn",
  "team_name": "research-team",
  "member_name": "Alice",
  "role": "researcher",
  "task": "Research EU climate policy and carbon pricing mechanisms"
}

{
  "action": "spawn",
  "team_name": "research-team",
  "member_name": "Bob",
  "role": "researcher",
  "task": "Research US climate policy and renewable subsidies"
}

{
  "action": "spawn",
  "team_name": "research-team",
  "member_name": "Charlie",
  "role": "researcher",
  "task": "Research China climate policy and emissions targets"
}
```

**Coordination:**

```python
# Check team status
{
  "action": "status",
  "team_name": "research-team"
}
# Response:
# {
#   "name": "research-team",
#   "status": "active",
#   "members": [
#     {"name": "Alice", "role": "researcher", "status": "active"},
#     {"name": "Bob", "role": "researcher", "status": "active"},
#     {"name": "Charlie", "role": "researcher", "status": "active"}
#   ]
# }

# Send a message to Alice (update focus area)
{
  "action": "send",
  "team_name": "research-team",
  "agent_id": "alice-id",
  "message": "Prioritize recent 2024 EU emissions data"
}

# Check Alice's inbox for completed research
{
  "action": "inbox",
  "team_name": "research-team",
  "agent_id": "alice-id"
}
# Returns pending messages in her inbox and clears them
```

### Example 2: Document Processing Pipeline

**Setup:** Team with specialized roles for document analysis.

```python
# Create team
{
  "action": "create",
  "team_name": "doc-pipeline",
  "description": "Multi-role document processing"
}

# Spawn members with different profiles
{
  "action": "spawn",
  "team_name": "doc-pipeline",
  "member_name": "Extractor",
  "role": "doc_extractor",  # Profile optimized for data extraction
  "task": "Extract structured data from legal documents"
}

{
  "action": "spawn",
  "team_name": "doc-pipeline",
  "member_name": "Analyst",
  "role": "analyst",  # Profile for analysis
  "task": "Analyze extracted data for risks and patterns"
}

{
  "action": "spawn",
  "team_name": "doc-pipeline",
  "member_name": "Reporter",
  "role": "report_writer",  # Profile for report generation
  "task": "Compile analysis into executive reports"
}
```

**Workflow:**

```
User Input
  │
  ├─ Send docs to Extractor
  │   └─ "Extract all contract terms from these 5 PDFs"
  │
  ├─ Extractor completes, posts to inbox
  │
  ├─ Read Extractor's inbox
  │   └─ Get structured data
  │
  ├─ Send to Analyst
  │   └─ "Analyze these contracts for liability risks"
  │
  ├─ Analyst completes, posts to inbox
  │
  ├─ Read Analyst's inbox
  │   └─ Get risk analysis
  │
  ├─ Send to Reporter
  │   └─ "Generate executive summary with risk matrix"
  │
  └─ Reporter completes
```

### Example 3: Team Coordination Script

```python
# ~/.operator/profiles/manager/workflows/research_team.py

meta = {
    "name": "research_team_workflow",
    "description": "Coordinate a research team to investigate a topic",
}

async def run():
    topic = args.get("topic", "quantum computing")
    team_name = f"research-{topic.replace(' ', '-')}"
    
    async with phase("setup"):
        log(f"Creating research team for: {topic}")
        # Create team
        await agent(
            f"{{action: create, team_name: {team_name}, "
            f"description: 'Research on {topic}'}}"
        )
    
    async with phase("spawn"):
        log("Spawning team members...")
        members = ["Alice", "Bob", "Charlie"]
        for name in members:
            await agent(
                f"{{action: spawn, team_name: {team_name}, "
                f"member_name: {name}, role: researcher, "
                f"task: 'Research {topic} from different angles'}}"
            )
    
    async with phase("coordinate"):
        log("Coordinating research...")
        # Get team status
        status = await agent(
            f"{{action: status, team_name: {team_name}}}"
        )
        
        # Send follow-up messages
        for member_id in status['member_ids']:
            await agent(
                f"{{action: send, team_name: {team_name}, "
                f"agent_id: {member_id}, "
                f"message: 'Focus on recent 2024 developments'}}"
            )
    
    async with phase("collect"):
        log("Collecting results from team members...")
        results = []
        for member_id in status['member_ids']:
            inbox = await agent(
                f"{{action: inbox, team_name: {team_name}, "
                f"agent_id: {member_id}}}"
            )
            results.append(inbox)
    
    async with phase("report"):
        log("Generating final report...")
        final_report = await agent(
            f"Write a comprehensive report synthesizing these "
            f"research findings: {results}"
        )
    
    return {
        "team": team_name,
        "topic": topic,
        "members": len(members),
        "report": final_report
    }
```

### Example 4: Dynamic Team Growth

```python
async def expand_team_if_needed(team_name, topic):
    """Add more researchers if initial team is overwhelmed."""
    status = await agent(f"{{action: status, team_name: {team_name}}}")
    
    active_members = len([m for m in status['members'] if m['status'] == 'active'])
    
    if active_members < 3:  # Scale up if needed
        await agent(
            f"{{action: spawn, team_name: {team_name}, "
            f"member_name: Dave, role: researcher, "
            f"task: 'Research {topic}'}}"
        )
        log(f"Scaled team to {active_members + 1} members")
```

### Inbox Message Format

```python
# Messages in inbox (stored as JSON files)
{
  "id": "msg-1234",
  "type": "message",  # "message" | "result" | "note"
  "sender_id": "root-session",
  "content": "Please research the latest developments",
  "created_at": 1718000000
}
```

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
