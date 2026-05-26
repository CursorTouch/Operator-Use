from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from program.agent.goals import GoalManager, parse_judge_response
from program.agent.types import PromptOptions
from program.message.types import AssistantMessage, TextContent
from program.session.manager import SessionManager


def _manager(judge=None, max_turns: int = 20) -> GoalManager:
    return GoalManager(
        SessionManager.in_memory(),
        judge=judge or AsyncMock(return_value=("continue", "not done", False)),
        default_max_turns=max_turns,
    )


class TestJudgeParsing:
    def test_parse_plain_json_done(self):
        done, reason, parse_failed = parse_judge_response('{"done": true, "reason": "finished"}')
        assert done is True
        assert reason == "finished"
        assert parse_failed is False

    def test_parse_embedded_json(self):
        done, reason, parse_failed = parse_judge_response('verdict: {"done": false, "reason": "more work"}')
        assert done is False
        assert reason == "more work"
        assert parse_failed is False

    def test_parse_failure_is_continue_shape(self):
        done, reason, parse_failed = parse_judge_response('not json')
        assert done is False
        assert "not JSON" in reason
        assert parse_failed is True


class TestGoalManager:
    def test_set_pause_resume_clear(self):
        mgr = _manager()
        state = mgr.set("finish the task")
        assert state.status == "active"
        assert mgr.is_active()

        mgr.pause()
        assert mgr.state is not None
        assert mgr.state.status == "paused"

        mgr.resume()
        assert mgr.state.status == "active"

        mgr.clear()
        assert mgr.state is None

    def test_persists_to_session_custom_info(self):
        sm = SessionManager.in_memory()
        mgr = GoalManager(sm, judge=AsyncMock(return_value=("continue", "not done", False)))
        mgr.set("persist me")

        loaded = GoalManager(sm, judge=AsyncMock(return_value=("continue", "not done", False)))

        assert loaded.state is not None
        assert loaded.state.goal == "persist me"

    @pytest.mark.asyncio
    async def test_evaluate_done_marks_done(self):
        mgr = _manager(judge=AsyncMock(return_value=("done", "complete", False)))
        mgr.set("do it")

        decision = await mgr.evaluate_after_turn("done")

        assert decision["should_continue"] is False
        assert decision["verdict"] == "done"
        assert mgr.state is not None
        assert mgr.state.status == "done"

    @pytest.mark.asyncio
    async def test_evaluate_continue_under_budget(self):
        mgr = _manager(judge=AsyncMock(return_value=("continue", "more work", False)), max_turns=3)
        mgr.set("long task")

        decision = await mgr.evaluate_after_turn("progress")

        assert decision["should_continue"] is True
        assert "long task" in decision["continuation_prompt"]
        assert mgr.state is not None
        assert mgr.state.turns_used == 1

    @pytest.mark.asyncio
    async def test_budget_exhaustion_pauses(self):
        mgr = _manager(judge=AsyncMock(return_value=("continue", "more work", False)), max_turns=1)
        mgr.set("long task")

        decision = await mgr.evaluate_after_turn("progress")

        assert decision["should_continue"] is False
        assert mgr.state is not None
        assert mgr.state.status == "paused"
        assert "budget" in (mgr.state.paused_reason or "")

    @pytest.mark.asyncio
    async def test_parse_failures_auto_pause(self):
        mgr = _manager(judge=AsyncMock(return_value=("continue", "bad json", True)), max_turns=10)
        mgr.set("long task")

        await mgr.evaluate_after_turn("progress")
        await mgr.evaluate_after_turn("progress")
        decision = await mgr.evaluate_after_turn("progress")

        assert decision["should_continue"] is False
        assert mgr.state is not None
        assert mgr.state.status == "paused"
        assert "unparseable" in (mgr.state.paused_reason or "")


class TestAgentGoalContinuation:
    @pytest.mark.asyncio
    async def test_agent_continues_goal_until_done(self):
        from helpers import FakeLLM, make_agent, text_seq

        llm = FakeLLM(text_seq("step one"), text_seq("step two"))
        agent, _ = make_agent(llm)
        judge = AsyncMock(side_effect=[
            ("continue", "one more step", False),
            ("done", "complete", False),
        ])
        agent._goal_manager = GoalManager(agent.session_manager, judge=judge, default_max_turns=5)
        agent.goal_manager.set("finish")

        await agent.invoke("finish", PromptOptions(source='goal'))

        assert llm.call_count == 2
        assert agent.goal_manager.state is not None
        assert agent.goal_manager.state.status == "done"
