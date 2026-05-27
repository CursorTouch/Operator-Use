"""Extensive tests for the team module and completion listeners.

Covers:
 - TeamMember / TeamRecord / MailboxMessage types
 - TeamMailbox: send, receive, peek, atomic ordering
 - TeamManager: CRUD, member management, persistence, mailbox delegation
 - Completion listeners on SubagentManager
 - TeamTool: all 7 actions, validation, error paths
 - SubagentRecord.team_id field
 - ToolContext.team_manager field
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from program.team.types import MailboxMessage, TeamMember, TeamRecord
from program.team.mailbox import TeamMailbox
from program.team.manager import TeamManager
from program.subagent.types import SubagentRecord, SubagentStatus, SubagentSettings
from program.tool.types import ToolContext, ToolInvocation


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _member(agent_id: str = "sub_abc", name: str = "researcher", role: str = "research") -> TeamMember:
    return TeamMember(agent_id=agent_id, name=name, role=role, joined_at=time.time())


def _record(task_id: str = "sub_001") -> SubagentRecord:
    return SubagentRecord(
        task_id=task_id,
        label="test",
        task="do stuff",
        status=SubagentStatus.running,
        started_at=datetime.now(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────────────

class TestTeamMember:
    def test_defaults(self):
        m = _member()
        assert m.status == "active"
        assert m.model is None

    def test_fields(self):
        m = TeamMember(agent_id="x", name="y", role="z", joined_at=1.0, status="idle", model="sonnet")
        assert m.status == "idle"
        assert m.model == "sonnet"


class TestTeamRecord:
    def test_defaults(self):
        r = TeamRecord(name="alpha", description="desc", created_at=1.0, creator_id="root")
        assert r.members == []
        assert r.status == "active"

    def test_custom_status(self):
        r = TeamRecord(name="beta", description="", created_at=1.0, creator_id="root", status="dissolved")
        assert r.status == "dissolved"


class TestMailboxMessage:
    def test_fields(self):
        m = MailboxMessage(id="m1", type="message", sender_id="root", content="hi", created_at=1.0)
        assert m.id == "m1"
        assert m.content == "hi"


# ─────────────────────────────────────────────────────────────────────────────
# TeamMailbox
# ─────────────────────────────────────────────────────────────────────────────

class TestTeamMailbox:
    @pytest.mark.asyncio
    async def test_send_creates_file(self, tmp_path):
        mb = TeamMailbox(tmp_path, "alpha", "sub_001")
        msg_id = await mb.send("message", "root", "hello")
        files = list((tmp_path / "alpha" / "agents" / "sub_001" / "inbox").glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["content"] == "hello"
        assert data["sender_id"] == "root"
        assert data["id"] == msg_id

    @pytest.mark.asyncio
    async def test_receive_returns_and_clears(self, tmp_path):
        mb = TeamMailbox(tmp_path, "alpha", "sub_001")
        await mb.send("note", "root", "msg1")
        await mb.send("note", "root", "msg2")
        msgs = await mb.receive()
        assert len(msgs) == 2
        assert all(isinstance(m, MailboxMessage) for m in msgs)
        # cleared
        msgs2 = await mb.receive()
        assert msgs2 == []

    @pytest.mark.asyncio
    async def test_peek_does_not_clear(self, tmp_path):
        mb = TeamMailbox(tmp_path, "alpha", "sub_001")
        await mb.send("note", "root", "persistent")
        msgs = await mb.peek()
        assert len(msgs) == 1
        msgs2 = await mb.peek()
        assert len(msgs2) == 1

    @pytest.mark.asyncio
    async def test_receive_empty_inbox(self, tmp_path):
        mb = TeamMailbox(tmp_path, "alpha", "sub_001")
        msgs = await mb.receive()
        assert msgs == []

    @pytest.mark.asyncio
    async def test_messages_ordered_by_timestamp(self, tmp_path):
        mb = TeamMailbox(tmp_path, "alpha", "sub_001")
        for i in range(5):
            await mb.send("note", "root", f"msg{i}")
        msgs = await mb.receive()
        contents = [m.content for m in msgs]
        assert contents == [f"msg{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_send_returns_unique_ids(self, tmp_path):
        mb = TeamMailbox(tmp_path, "alpha", "sub_001")
        ids = [await mb.send("note", "root", "x") for _ in range(10)]
        assert len(set(ids)) == 10

    @pytest.mark.asyncio
    async def test_separate_agents_have_independent_inboxes(self, tmp_path):
        mb1 = TeamMailbox(tmp_path, "alpha", "agent_a")
        mb2 = TeamMailbox(tmp_path, "alpha", "agent_b")
        await mb1.send("msg", "root", "for a")
        await mb2.send("msg", "root", "for b")
        msgs_a = await mb1.receive()
        msgs_b = await mb2.receive()
        assert len(msgs_a) == 1 and msgs_a[0].content == "for a"
        assert len(msgs_b) == 1 and msgs_b[0].content == "for b"

    @pytest.mark.asyncio
    async def test_message_type_preserved(self, tmp_path):
        mb = TeamMailbox(tmp_path, "alpha", "sub_001")
        await mb.send("result", "worker", "done")
        msgs = await mb.receive()
        assert msgs[0].type == "result"
        assert msgs[0].sender_id == "worker"

    @pytest.mark.asyncio
    async def test_inbox_dir_created_automatically(self, tmp_path):
        deep = tmp_path / "nested" / "path"
        mb = TeamMailbox(deep, "alpha", "sub_001")
        await mb.send("msg", "root", "test")
        inbox = deep / "alpha" / "agents" / "sub_001" / "inbox"
        assert inbox.is_dir()

    @pytest.mark.asyncio
    async def test_atomic_write_no_tmp_files_left(self, tmp_path):
        mb = TeamMailbox(tmp_path, "alpha", "sub_001")
        await mb.send("msg", "root", "data")
        inbox = tmp_path / "alpha" / "agents" / "sub_001" / "inbox"
        tmp_files = list(inbox.glob(".*.tmp"))
        assert tmp_files == []


# ─────────────────────────────────────────────────────────────────────────────
# TeamManager
# ─────────────────────────────────────────────────────────────────────────────

class TestTeamManagerCRUD:
    def test_create_team(self, tmp_path):
        mgr = TeamManager(tmp_path)
        team = mgr.create("alpha", "test team")
        assert team.name == "alpha"
        assert team.description == "test team"
        assert team.status == "active"
        assert team.creator_id == "root"
        assert team.members == []

    def test_create_duplicate_raises(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "first")
        with pytest.raises(ValueError, match="already exists"):
            mgr.create("alpha", "second")

    def test_get_existing_team(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        t = mgr.get("alpha")
        assert t is not None
        assert t.name == "alpha"

    def test_get_nonexistent_returns_none(self, tmp_path):
        mgr = TeamManager(tmp_path)
        assert mgr.get("ghost") is None

    def test_list_teams_empty(self, tmp_path):
        mgr = TeamManager(tmp_path)
        assert mgr.list_teams() == []

    def test_list_teams_multiple(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "a")
        mgr.create("beta", "b")
        mgr.create("gamma", "c")
        names = {t.name for t in mgr.list_teams()}
        assert names == {"alpha", "beta", "gamma"}

    def test_dissolve_team(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.dissolve("alpha")
        assert mgr.get("alpha").status == "dissolved"

    def test_dissolve_nonexistent_raises(self, tmp_path):
        mgr = TeamManager(tmp_path)
        with pytest.raises(ValueError, match="No team"):
            mgr.dissolve("ghost")

    def test_create_with_custom_creator_id(self, tmp_path):
        mgr = TeamManager(tmp_path)
        team = mgr.create("alpha", "desc", creator_id="sub_xyz")
        assert team.creator_id == "sub_xyz"


class TestTeamManagerMembers:
    def test_add_member(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001", "Alice", "researcher"))
        team = mgr.get("alpha")
        assert len(team.members) == 1
        assert team.members[0].name == "Alice"

    def test_add_multiple_members(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        for i in range(4):
            mgr.add_member("alpha", _member(f"sub_{i:03d}", f"member{i}"))
        assert len(mgr.get("alpha").members) == 4

    def test_add_member_nonexistent_team_raises(self, tmp_path):
        mgr = TeamManager(tmp_path)
        with pytest.raises(ValueError, match="No team"):
            mgr.add_member("ghost", _member())

    def test_get_member(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001", "Alice"))
        m = mgr.get_member("alpha", "sub_001")
        assert m is not None
        assert m.name == "Alice"

    def test_get_member_missing_returns_none(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        assert mgr.get_member("alpha", "no_such") is None

    def test_get_member_nonexistent_team_returns_none(self, tmp_path):
        mgr = TeamManager(tmp_path)
        assert mgr.get_member("ghost", "sub_001") is None

    def test_find_member_by_name(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001", "Alice"))
        m = mgr.find_member_by_name("alpha", "alice")  # case-insensitive
        assert m is not None
        assert m.agent_id == "sub_001"

    def test_find_member_by_name_missing(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        assert mgr.find_member_by_name("alpha", "Bob") is None

    def test_update_member_status(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001"))
        mgr.update_member_status("alpha", "sub_001", "stopped")
        assert mgr.get_member("alpha", "sub_001").status == "stopped"

    def test_update_member_status_nonexistent_team_is_noop(self, tmp_path):
        mgr = TeamManager(tmp_path)
        # Should not raise
        mgr.update_member_status("ghost", "sub_001", "stopped")

    def test_update_member_status_nonexistent_member_is_noop(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.update_member_status("alpha", "no_such", "stopped")


class TestTeamManagerPersistence:
    def test_teams_persist_across_instances(self, tmp_path):
        mgr1 = TeamManager(tmp_path)
        mgr1.create("alpha", "test team", creator_id="root")
        mgr1.add_member("alpha", _member("sub_001", "Alice", "research"))

        mgr2 = TeamManager(tmp_path)
        team = mgr2.get("alpha")
        assert team is not None
        assert team.description == "test team"
        assert len(team.members) == 1
        assert team.members[0].name == "Alice"

    def test_multiple_teams_persist(self, tmp_path):
        mgr1 = TeamManager(tmp_path)
        mgr1.create("alpha", "a")
        mgr1.create("beta", "b")

        mgr2 = TeamManager(tmp_path)
        assert {t.name for t in mgr2.list_teams()} == {"alpha", "beta"}

    def test_member_status_persists(self, tmp_path):
        mgr1 = TeamManager(tmp_path)
        mgr1.create("alpha", "desc")
        mgr1.add_member("alpha", _member("sub_001"))
        mgr1.update_member_status("alpha", "sub_001", "stopped")

        mgr2 = TeamManager(tmp_path)
        assert mgr2.get_member("alpha", "sub_001").status == "stopped"

    def test_dissolved_status_persists(self, tmp_path):
        mgr1 = TeamManager(tmp_path)
        mgr1.create("alpha", "desc")
        mgr1.dissolve("alpha")

        mgr2 = TeamManager(tmp_path)
        assert mgr2.get("alpha").status == "dissolved"

    def test_team_json_is_valid_json(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001", "Alice"))
        team_json = tmp_path / "alpha" / "team.json"
        assert team_json.exists()
        data = json.loads(team_json.read_text())
        assert data["name"] == "alpha"
        assert len(data["members"]) == 1

    def test_corrupt_team_file_is_skipped_on_load(self, tmp_path):
        # Write a corrupt JSON file
        team_dir = tmp_path / "bad_team"
        team_dir.mkdir()
        (team_dir / "team.json").write_text("not valid json", encoding="utf-8")
        mgr = TeamManager(tmp_path)
        assert mgr.get("bad_team") is None

    def test_no_tmp_files_after_save(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        tmp_files = list(tmp_path.glob("**/.team.json.tmp"))
        assert tmp_files == []

    def test_creator_id_persists(self, tmp_path):
        mgr1 = TeamManager(tmp_path)
        mgr1.create("alpha", "desc", creator_id="sub_xyz")
        mgr2 = TeamManager(tmp_path)
        assert mgr2.get("alpha").creator_id == "sub_xyz"


class TestTeamManagerMailbox:
    @pytest.mark.asyncio
    async def test_send_message(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001"))
        msg_id = await mgr.send_message("alpha", "sub_001", "root", "message", "hello")
        assert isinstance(msg_id, str)

    @pytest.mark.asyncio
    async def test_read_inbox(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001"))
        await mgr.send_message("alpha", "sub_001", "root", "message", "hello")
        msgs = await mgr.read_inbox("alpha", "sub_001")
        assert len(msgs) == 1
        assert msgs[0].content == "hello"

    @pytest.mark.asyncio
    async def test_read_inbox_clears_messages(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001"))
        await mgr.send_message("alpha", "sub_001", "root", "message", "once")
        await mgr.read_inbox("alpha", "sub_001")
        msgs = await mgr.read_inbox("alpha", "sub_001")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_peek_inbox_does_not_clear(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001"))
        await mgr.send_message("alpha", "sub_001", "root", "message", "persistent")
        await mgr.peek_inbox("alpha", "sub_001")
        msgs = await mgr.peek_inbox("alpha", "sub_001")
        assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_multiple_messages_to_same_inbox(self, tmp_path):
        mgr = TeamManager(tmp_path)
        mgr.create("alpha", "desc")
        mgr.add_member("alpha", _member("sub_001"))
        for i in range(5):
            await mgr.send_message("alpha", "sub_001", "root", "note", f"msg{i}")
        msgs = await mgr.read_inbox("alpha", "sub_001")
        assert len(msgs) == 5


# ─────────────────────────────────────────────────────────────────────────────
# Completion listeners on SubagentManager
# ─────────────────────────────────────────────────────────────────────────────

class TestCompletionListeners:
    def _make_manager(self):
        from program.subagent.manager import SubagentManager
        mgr = SubagentManager.__new__(SubagentManager)
        from collections import defaultdict
        mgr._listeners = defaultdict(list)
        return mgr

    def test_on_complete_registers_callback(self):
        from program.subagent.manager import SubagentManager
        mgr = self._make_manager()
        cb = AsyncMock()
        mgr.on_complete("sub_001", cb)
        assert len(mgr._listeners["sub_001"]) == 1

    def test_on_complete_multiple_callbacks_same_task(self):
        mgr = self._make_manager()
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        mgr.on_complete("sub_001", cb1)
        mgr.on_complete("sub_001", cb2)
        assert len(mgr._listeners["sub_001"]) == 2

    def test_on_complete_different_tasks_independent(self):
        mgr = self._make_manager()
        mgr.on_complete("sub_001", AsyncMock())
        mgr.on_complete("sub_002", AsyncMock())
        assert len(mgr._listeners["sub_001"]) == 1
        assert len(mgr._listeners["sub_002"]) == 1

    @pytest.mark.asyncio
    async def test_listener_fires_on_task_completion(self):
        from program.subagent.manager import SubagentManager
        from program.subagent.types import SubagentSettings

        # Build a minimal manager with a mock runner and bus
        class _FakeLLM:
            model = MagicMock(name="fake")

        manager = SubagentManager(
            llm=_FakeLLM(),
            tools=[],
            bus=None,
        )

        fired = []

        async def cb(record):
            fired.append(record.task_id)

        record = _record("sub_aaa")
        record.status = SubagentStatus.completed
        record.result = "done"
        manager._records["sub_aaa"] = record
        manager.on_complete("sub_aaa", cb)

        # Simulate the announce flow (bus is None, so announce is a noop)
        await manager._run_and_announce(record)

        assert "sub_aaa" in fired

    @pytest.mark.asyncio
    async def test_listener_fires_once_then_cleared(self):
        from program.subagent.manager import SubagentManager

        class _FakeLLM:
            model = MagicMock(name="fake")

        manager = SubagentManager(llm=_FakeLLM(), tools=[], bus=None)
        fired_count = [0]

        async def cb(record):
            fired_count[0] += 1

        record = _record("sub_bbb")
        record.status = SubagentStatus.completed
        manager._records["sub_bbb"] = record
        manager.on_complete("sub_bbb", cb)

        await manager._run_and_announce(record)
        await manager._run_and_announce(record)  # second run, listener already cleared

        assert fired_count[0] == 1

    @pytest.mark.asyncio
    async def test_listener_exception_does_not_propagate(self):
        from program.subagent.manager import SubagentManager

        class _FakeLLM:
            model = MagicMock(name="fake")

        manager = SubagentManager(llm=_FakeLLM(), tools=[], bus=None)

        async def bad_cb(record):
            raise RuntimeError("listener error")

        record = _record("sub_ccc")
        record.status = SubagentStatus.failed
        manager._records["sub_ccc"] = record
        manager.on_complete("sub_ccc", bad_cb)

        # Should not raise
        await manager._run_and_announce(record)

    @pytest.mark.asyncio
    async def test_multiple_listeners_all_fire(self):
        from program.subagent.manager import SubagentManager

        class _FakeLLM:
            model = MagicMock(name="fake")

        manager = SubagentManager(llm=_FakeLLM(), tools=[], bus=None)
        results = []

        for i in range(3):
            async def cb(record, i=i):
                results.append(i)
            manager.on_complete("sub_ddd", cb)

        record = _record("sub_ddd")
        record.status = SubagentStatus.completed
        manager._records["sub_ddd"] = record
        await manager._run_and_announce(record)

        assert sorted(results) == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_listener_receives_correct_record(self):
        from unittest.mock import patch
        from program.subagent.manager import SubagentManager

        class _FakeLLM:
            model = MagicMock(name="fake")

        manager = SubagentManager(llm=_FakeLLM(), tools=[], bus=None)
        received = []

        async def cb(record):
            received.append(record)

        record = _record("sub_eee")
        record.status = SubagentStatus.completed
        record.result = "final answer"
        manager._records["sub_eee"] = record
        manager.on_complete("sub_eee", cb)

        # Patch the runner so it doesn't overwrite record.result
        async def _noop_run(r):
            pass

        with patch.object(manager._runner, "run", side_effect=_noop_run):
            await manager._run_and_announce(record)

        assert len(received) == 1
        assert received[0].task_id == "sub_eee"
        assert received[0].result == "final answer"


# ─────────────────────────────────────────────────────────────────────────────
# SubagentRecord.team_id field
# ─────────────────────────────────────────────────────────────────────────────

class TestSubagentRecordTeamId:
    def test_team_id_defaults_to_none(self):
        rec = _record()
        assert rec.team_id is None

    def test_team_id_can_be_set(self):
        rec = SubagentRecord(
            task_id="sub_001",
            label="test",
            task="do it",
            status=SubagentStatus.running,
            started_at=datetime.now(),
            team_id="alpha",
        )
        assert rec.team_id == "alpha"

    def test_team_id_is_string_or_none(self):
        rec = SubagentRecord(
            task_id="t1", label="l", task="t",
            status=SubagentStatus.running, started_at=datetime.now(),
            team_id="my-team",
        )
        assert isinstance(rec.team_id, str)


# ─────────────────────────────────────────────────────────────────────────────
# ToolContext.team_manager
# ─────────────────────────────────────────────────────────────────────────────

class TestToolContextTeamManager:
    def test_team_manager_field_exists(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ToolContext)}
        assert "team_manager" in fields

    def test_team_manager_defaults_to_none(self):
        ctx = ToolContext()
        assert ctx.team_manager is None

    def test_team_manager_can_be_set(self, tmp_path):
        mgr = TeamManager(tmp_path)
        ctx = ToolContext(team_manager=mgr)
        assert ctx.team_manager is mgr


# ─────────────────────────────────────────────────────────────────────────────
# TeamTool — all actions
# ─────────────────────────────────────────────────────────────────────────────

def _make_context(tmp_path, with_subagent=True) -> ToolContext:
    tm = TeamManager(tmp_path)
    ctx = ToolContext(team_manager=tm, spawn_depth=0)
    if with_subagent:
        from program.subagent.types import SubagentSettings
        sub_mgr = MagicMock()
        sub_mgr._settings = SubagentSettings(max_spawn_depth=3)
        sub_mgr.invoke = AsyncMock(return_value="sub_spawn001")
        sub_mgr.on_complete = MagicMock()
        ctx.subagent_manager = sub_mgr
    return ctx


def _inv(action: str, **kwargs) -> ToolInvocation:
    return ToolInvocation(id="inv1", name="team", params={"action": action, **kwargs})


class TestTeamToolList:
    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("list"), context=ctx)
        assert not r.is_error
        assert "No teams" in r.content

    @pytest.mark.asyncio
    async def test_list_shows_teams(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "test")
        ctx.team_manager.create("beta", "other")
        r = await tool.execute(_inv("list"), context=ctx)
        assert "alpha" in r.content
        assert "beta" in r.content

    @pytest.mark.asyncio
    async def test_list_shows_member_count(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.team_manager.add_member("alpha", _member("sub_001"))
        ctx.team_manager.add_member("alpha", _member("sub_002", "Bob"))
        r = await tool.execute(_inv("list"), context=ctx)
        assert "2 members" in r.content


class TestTeamToolCreate:
    @pytest.mark.asyncio
    async def test_create_success(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("create", team_name="alpha", description="test"), context=ctx)
        assert not r.is_error
        assert "alpha" in r.content
        assert ctx.team_manager.get("alpha") is not None

    @pytest.mark.asyncio
    async def test_create_missing_team_name(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("create"), context=ctx)
        assert r.is_error
        assert "team_name" in r.content

    @pytest.mark.asyncio
    async def test_create_duplicate_returns_error(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        await tool.execute(_inv("create", team_name="alpha", description=""), context=ctx)
        r = await tool.execute(_inv("create", team_name="alpha", description=""), context=ctx)
        assert r.is_error
        assert "already exists" in r.content


class TestTeamToolSpawn:
    @pytest.mark.asyncio
    async def test_spawn_success(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Alice", role="research", task="do research"),
            context=ctx,
        )
        assert not r.is_error
        assert "Alice" in r.content
        assert "sub_spawn001" in r.content
        # Member added to team
        member = ctx.team_manager.get_member("alpha", "sub_spawn001")
        assert member is not None
        assert member.name == "Alice"

    @pytest.mark.asyncio
    async def test_spawn_missing_team_name(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("spawn", member_name="Alice", role="r", task="t"), context=ctx)
        assert r.is_error

    @pytest.mark.asyncio
    async def test_spawn_nonexistent_team(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(
            _inv("spawn", team_name="ghost", member_name="Alice", role="r", task="t"),
            context=ctx,
        )
        assert r.is_error
        assert "ghost" in r.content

    @pytest.mark.asyncio
    async def test_spawn_missing_role(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Alice", task="t"),
            context=ctx,
        )
        assert r.is_error

    @pytest.mark.asyncio
    async def test_spawn_missing_task(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Alice", role="r"),
            context=ctx,
        )
        assert r.is_error

    @pytest.mark.asyncio
    async def test_spawn_depth_limit_blocks(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.spawn_depth = 3  # at max
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Alice", role="r", task="t"),
            context=ctx,
        )
        assert r.is_error
        assert "depth" in r.content.lower()

    @pytest.mark.asyncio
    async def test_spawn_calls_subagent_manager(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Alice", role="research", task="do it"),
            context=ctx,
        )
        ctx.subagent_manager.invoke.assert_called_once()
        call_kwargs = ctx.subagent_manager.invoke.call_args
        assert call_kwargs.kwargs.get("team_id") == "alpha"
        assert call_kwargs.kwargs.get("profile") == "research"

    @pytest.mark.asyncio
    async def test_spawn_registers_completion_listener(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Alice", role="r", task="t"),
            context=ctx,
        )
        ctx.subagent_manager.on_complete.assert_called_once_with("sub_spawn001", ctx.subagent_manager.on_complete.call_args[0][1])

    @pytest.mark.asyncio
    async def test_spawn_result_is_terminate(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Alice", role="r", task="t"),
            context=ctx,
        )
        assert r.terminate is True

    @pytest.mark.asyncio
    async def test_spawn_subagent_invoke_error_returns_tool_error(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.subagent_manager.invoke = AsyncMock(side_effect=ValueError("bad profile"))
        r = await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Alice", role="bad", task="t"),
            context=ctx,
        )
        assert r.is_error
        assert "bad profile" in r.content


class TestTeamToolSend:
    @pytest.mark.asyncio
    async def test_send_success(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.team_manager.add_member("alpha", _member("sub_001"))
        r = await tool.execute(
            _inv("send", team_name="alpha", agent_id="sub_001", message="hello!"),
            context=ctx,
        )
        assert not r.is_error
        assert "sub_001" in r.content

    @pytest.mark.asyncio
    async def test_send_message_actually_stored(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.team_manager.add_member("alpha", _member("sub_001"))
        await tool.execute(
            _inv("send", team_name="alpha", agent_id="sub_001", message="stored msg"),
            context=ctx,
        )
        msgs = await ctx.team_manager.read_inbox("alpha", "sub_001")
        assert len(msgs) == 1
        assert msgs[0].content == "stored msg"

    @pytest.mark.asyncio
    async def test_send_missing_agent_id(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(_inv("send", team_name="alpha", message="hi"), context=ctx)
        assert r.is_error
        assert "agent_id" in r.content

    @pytest.mark.asyncio
    async def test_send_missing_message(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(_inv("send", team_name="alpha", agent_id="sub_001"), context=ctx)
        assert r.is_error
        assert "message" in r.content

    @pytest.mark.asyncio
    async def test_send_nonexistent_team(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(
            _inv("send", team_name="ghost", agent_id="sub_001", message="hi"),
            context=ctx,
        )
        assert r.is_error
        assert "ghost" in r.content


class TestTeamToolInbox:
    @pytest.mark.asyncio
    async def test_inbox_empty(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.team_manager.add_member("alpha", _member("sub_001"))
        r = await tool.execute(_inv("inbox", team_name="alpha", agent_id="sub_001"), context=ctx)
        assert not r.is_error
        assert "No messages" in r.content

    @pytest.mark.asyncio
    async def test_inbox_shows_messages(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.team_manager.add_member("alpha", _member("sub_001"))
        await ctx.team_manager.send_message("alpha", "sub_001", "root", "message", "check this out")
        r = await tool.execute(_inv("inbox", team_name="alpha", agent_id="sub_001"), context=ctx)
        assert not r.is_error
        assert "check this out" in r.content

    @pytest.mark.asyncio
    async def test_inbox_clears_after_read(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.team_manager.add_member("alpha", _member("sub_001"))
        await ctx.team_manager.send_message("alpha", "sub_001", "root", "message", "once")
        await tool.execute(_inv("inbox", team_name="alpha", agent_id="sub_001"), context=ctx)
        r2 = await tool.execute(_inv("inbox", team_name="alpha", agent_id="sub_001"), context=ctx)
        assert "No messages" in r2.content

    @pytest.mark.asyncio
    async def test_inbox_missing_agent_id(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(_inv("inbox", team_name="alpha"), context=ctx)
        assert r.is_error

    @pytest.mark.asyncio
    async def test_inbox_nonexistent_team(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("inbox", team_name="ghost", agent_id="sub_001"), context=ctx)
        assert r.is_error
        assert "ghost" in r.content


class TestTeamToolStatus:
    @pytest.mark.asyncio
    async def test_status_shows_team_info(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "my team")
        r = await tool.execute(_inv("status", team_name="alpha"), context=ctx)
        assert not r.is_error
        assert "alpha" in r.content
        assert "my team" in r.content

    @pytest.mark.asyncio
    async def test_status_shows_members(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.team_manager.add_member("alpha", _member("sub_001", "Alice", "research"))
        ctx.team_manager.add_member("alpha", _member("sub_002", "Bob", "writer"))
        r = await tool.execute(_inv("status", team_name="alpha"), context=ctx)
        assert "Alice" in r.content
        assert "Bob" in r.content
        assert "sub_001" in r.content

    @pytest.mark.asyncio
    async def test_status_nonexistent_team(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("status", team_name="ghost"), context=ctx)
        assert r.is_error
        assert "ghost" in r.content

    @pytest.mark.asyncio
    async def test_status_shows_inbox_count(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        ctx.team_manager.add_member("alpha", _member("sub_001"))
        await ctx.team_manager.send_message("alpha", "sub_001", "root", "message", "pending")
        r = await tool.execute(_inv("status", team_name="alpha"), context=ctx)
        assert "1 message" in r.content

    @pytest.mark.asyncio
    async def test_status_missing_team_name(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("status"), context=ctx)
        assert r.is_error


class TestTeamToolDissolve:
    @pytest.mark.asyncio
    async def test_dissolve_success(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        ctx.team_manager.create("alpha", "desc")
        r = await tool.execute(_inv("dissolve", team_name="alpha"), context=ctx)
        assert not r.is_error
        assert ctx.team_manager.get("alpha").status == "dissolved"

    @pytest.mark.asyncio
    async def test_dissolve_nonexistent_team(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("dissolve", team_name="ghost"), context=ctx)
        assert r.is_error

    @pytest.mark.asyncio
    async def test_dissolve_missing_team_name(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(_inv("dissolve"), context=ctx)
        assert r.is_error


class TestTeamToolAvailability:
    def test_not_available_without_team_manager(self):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = ToolContext()  # team_manager=None
        assert not tool.is_available(ctx)

    def test_available_with_team_manager(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = ToolContext(team_manager=TeamManager(tmp_path))
        assert tool.is_available(ctx)

    @pytest.mark.asyncio
    async def test_execute_without_context_returns_error(self):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        r = await tool.execute(_inv("list"), context=None)
        assert r.is_error

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, tmp_path):
        from program.builtins.tools.team import TeamTool
        tool = TeamTool()
        ctx = _make_context(tmp_path)
        r = await tool.execute(ToolInvocation(id="i", name="team", params={"action": "nope"}), context=ctx)
        assert r.is_error
        assert "nope" in r.content


# ─────────────────────────────────────────────────────────────────────────────
# Completion listener wires team member status update
# ─────────────────────────────────────────────────────────────────────────────

class TestTeamSpawnCompletionWiring:
    @pytest.mark.asyncio
    async def test_completion_updates_member_status(self, tmp_path):
        """When a spawned subagent finishes, team member status → stopped."""
        from program.builtins.tools.team import TeamTool
        from program.subagent.manager import SubagentManager
        from collections import defaultdict

        tool = TeamTool()

        # Build a real-ish SubagentManager but intercept on_complete
        class _FakeLLM:
            model = MagicMock(name="fake")

        real_sub_mgr = SubagentManager(llm=_FakeLLM(), tools=[], bus=None)

        tm = TeamManager(tmp_path)
        ctx = ToolContext(
            team_manager=tm,
            subagent_manager=real_sub_mgr,
            spawn_depth=0,
        )

        # Override invoke to return a fixed task_id without actually running anything
        real_sub_mgr.invoke = AsyncMock(return_value="sub_test_wired")

        tm.create("alpha", "wiring test")
        await tool.execute(
            _inv("spawn", team_name="alpha", member_name="Worker", role="r", task="t"),
            context=ctx,
        )

        # Member should be active initially
        assert tm.get_member("alpha", "sub_test_wired").status == "active"

        # Now fire the registered listener manually
        listeners = real_sub_mgr._listeners.get("sub_test_wired", [])
        assert len(listeners) == 1
        fake_record = _record("sub_test_wired")
        fake_record.status = SubagentStatus.completed
        await listeners[0](fake_record)

        # Status should now be stopped
        assert tm.get_member("alpha", "sub_test_wired").status == "stopped"
