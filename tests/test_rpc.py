"""RPC — command types, parsing, server dispatch logic (no real stdin/stdout needed)."""
from __future__ import annotations

import json

import pytest

from operator_use.rpc.types import (
    CommandType,
    PromptCommand, SteerCommand, FollowUpCommand, AbortCommand,
    NewSessionCommand, SwitchSessionCommand, ForkCommand,
    SetSessionNameCommand, GetStateCommand, GetMessagesCommand,
    GetLastAssistantTextCommand, SetModelCommand, SetThinkingLevelCommand,
    CompactCommand, SetAutoCompactionCommand,
    OkResponse, ErrorResponse,
    RPCEvent,
)


class TestCommandTypes:
    def test_all_command_types_exist(self):
        names = [ct.value for ct in CommandType]
        assert "prompt" in names
        assert "steer" in names
        assert "follow_up" in names
        assert "abort" in names
        assert "new_session" in names
        assert "switch_session" in names
        assert "fork" in names
        assert "set_session_name" in names
        assert "get_state" in names
        assert "get_messages" in names
        assert "get_last_assistant_text" in names
        assert "set_model" in names
        assert "set_thinking_level" in names
        assert "compact" in names
        assert "set_auto_compaction" in names

    def test_prompt_command_construction(self):
        cmd = PromptCommand(message="hello world", id="r1")
        assert cmd.type == CommandType.Prompt
        assert cmd.message == "hello world"

    def test_steer_command_construction(self):
        cmd = SteerCommand(message="steer this", id="r2")
        assert cmd.type == CommandType.Steer

    def test_follow_up_command_construction(self):
        cmd = FollowUpCommand(message="follow up", id="r3")
        assert cmd.type == CommandType.FollowUp

    def test_abort_command_construction(self):
        cmd = AbortCommand(id="r4")
        assert cmd.type == CommandType.Abort

    def test_new_session_command(self):
        cmd = NewSessionCommand(id="r5")
        assert cmd.type == CommandType.NewSession

    def test_switch_session_command(self):
        cmd = SwitchSessionCommand(session_path="/path/to/session.jsonl", id="r6")
        assert cmd.type == CommandType.SwitchSession

    def test_fork_command(self):
        cmd = ForkCommand(entry_id="entry-123", id="r7")
        assert cmd.type == CommandType.Fork

    def test_set_session_name_command(self):
        cmd = SetSessionNameCommand(name="My Session", id="r8")
        assert cmd.type == CommandType.SetSessionName

    def test_get_state_command(self):
        cmd = GetStateCommand(id="r9")
        assert cmd.type == CommandType.GetState

    def test_set_model_command(self):
        cmd = SetModelCommand(model_id="gpt-4o", provider="openai", id="r10")
        assert cmd.type == CommandType.SetModel

    def test_set_thinking_level_command(self):
        cmd = SetThinkingLevelCommand(level="high", id="r11")
        assert cmd.type == CommandType.SetThinkingLevel

    def test_compact_command(self):
        cmd = CompactCommand(id="r12")
        assert cmd.type == CommandType.Compact

    def test_set_auto_compaction_command(self):
        cmd = SetAutoCompactionCommand(enabled=True, id="r13")
        assert cmd.type == CommandType.SetAutoCompaction


class TestResponseTypes:
    def test_ok_response(self):
        resp = OkResponse(command="prompt", id="r1")
        assert resp.type == "response"
        assert resp.success is True

    def test_ok_response_with_data(self):
        resp = OkResponse(command="prompt", id="r1", data={"key": "value"})
        assert resp.data == {"key": "value"}

    def test_error_response(self):
        resp = ErrorResponse(command="prompt", error="something went wrong", id="r1")
        assert resp.type == "response"
        assert resp.success is False
        assert resp.error == "something went wrong"


class TestRPCEventType:
    def test_rpc_event_construction(self):
        event = RPCEvent(type="agent_start", data={})
        assert event.type == "agent_start"

    def test_rpc_event_with_data(self):
        event = RPCEvent(type="message_end", data={"role": "assistant"})
        assert event.data["role"] == "assistant"


class TestRPCCommandSerialization:
    def test_prompt_command_has_correct_fields(self):
        cmd = PromptCommand(message="hello", id="r1")
        assert cmd.message == "hello"
        assert cmd.id == "r1"
        assert cmd.type == CommandType.Prompt

    def test_set_model_has_correct_fields(self):
        cmd = SetModelCommand(model_id="m", provider="p", id="r1")
        assert cmd.model_id == "m"
        assert cmd.provider == "p"

    def test_switch_session_path_field(self):
        cmd = SwitchSessionCommand(session_path="/tmp/sess.jsonl")
        assert cmd.session_path == "/tmp/sess.jsonl"

    def test_ok_response_fields(self):
        resp = OkResponse(command="get_state", id="x", data={"state": 1})
        assert resp.command == "get_state"
        assert resp.success is True

    def test_error_response_fields(self):
        resp = ErrorResponse(command="prompt", error="bad input", id="x")
        assert resp.command == "prompt"
        assert resp.error == "bad input"
        assert resp.success is False
