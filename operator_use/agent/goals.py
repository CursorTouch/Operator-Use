from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Literal

from operator_use.inference.types import LLMContext
from operator_use.message.types import TextContent, UserMessage
from operator_use.session.types import CustomInfoEntry


DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3
GOAL_CUSTOM_TYPE = "goal"
JUDGE_RESPONSE_SNIPPET_CHARS = 4000

CONTINUATION_PROMPT_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n\n"
    "Continue working toward this goal. Take the next concrete step. "
    "If you believe the goal is complete, state so explicitly and stop. "
    "If you are blocked and need input from the user, say so clearly and stop."
)

CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n"
    "Required criteria:\n{subgoals}\n\n"
    "Continue working toward this goal. ALL criteria above must be satisfied before stopping. "
    "If you believe both the goal and all criteria are met, state so explicitly and stop. "
    "If you are blocked and need input from the user, say so clearly and stop."
)

JUDGE_SYSTEM_PROMPT = (
    "You are a strict judge evaluating whether an autonomous agent has "
    "achieved a user's stated goal. You receive the goal text and the "
    "agent's most recent response.\n\n"
    "A goal is DONE only when:\n"
    "- The response explicitly confirms the goal was completed, OR\n"
    "- The response clearly shows the final deliverable was produced, OR\n"
    "- The response explains the goal is unachievable / blocked / needs "
    "user input (treat this as DONE with a block reason).\n\n"
    "Otherwise the goal is NOT done - CONTINUE.\n\n"
    "Reply ONLY with a single JSON object on one line:\n"
    '{"done": <bool>, "reason": "<one-sentence rationale>"}'
)

JUDGE_SYSTEM_PROMPT_WITH_SUBGOALS = (
    "You are a strict judge evaluating whether an autonomous agent has "
    "achieved a user's stated goal AND all required criteria. You receive the goal, "
    "the criteria list, and the agent's most recent response.\n\n"
    "The goal is DONE only when ALL of the following are satisfied:\n"
    "- The primary goal is achieved (response confirms completion or shows the deliverable), AND\n"
    "- Every criterion in the list is explicitly satisfied.\n\n"
    "If the primary goal is done but any criterion is unmet — NOT done, CONTINUE.\n"
    "If blocked or needs user input — treat as DONE with a block reason.\n\n"
    "Reply ONLY with a single JSON object on one line:\n"
    '{"done": <bool>, "reason": "<one-sentence rationale>"}'
)

JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Is the goal satisfied?"
)

JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Required criteria:\n{subgoals}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Are the goal AND all criteria satisfied?"
)

JudgeFn = Callable[[str, str, "list[str] | None"], Awaitable[tuple[str, str, bool]]]


@dataclass
class GoalState:
    goal: str
    status: Literal["active", "paused", "done", "cleared"] = "active"
    turns_used: int = 0
    max_turns: int = DEFAULT_MAX_TURNS
    created_at: float = 0.0
    last_turn_at: float = 0.0
    last_verdict: str | None = None
    last_reason: str | None = None
    paused_reason: str | None = None
    consecutive_parse_failures: int = 0
    subgoals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalState:
        return cls(
            goal=str(data.get("goal", "")),
            status=data.get("status", "active"),
            turns_used=int(data.get("turns_used", 0) or 0),
            max_turns=int(data.get("max_turns", DEFAULT_MAX_TURNS) or DEFAULT_MAX_TURNS),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_turn_at=float(data.get("last_turn_at", 0.0) or 0.0),
            last_verdict=data.get("last_verdict"),
            last_reason=data.get("last_reason"),
            paused_reason=data.get("paused_reason"),
            consecutive_parse_failures=int(data.get("consecutive_parse_failures", 0) or 0),
            subgoals=list(data.get("subgoals") or []),
        )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "... [truncated]"


_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)


def parse_judge_response(raw: str) -> tuple[bool, str, bool]:
    if not raw:
        return False, "judge returned empty response", True

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        newline = text.find("\n")
        if newline != -1:
            text = text[newline + 1:]

    data: dict[str, Any] | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            data = parsed
    except Exception:
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = None

    if data is None:
        return False, f"judge reply was not JSON: {_truncate(raw, 200)!r}", True

    done_val = data.get("done")
    if isinstance(done_val, str):
        done = done_val.strip().lower() in {"true", "yes", "1", "done"}
    else:
        done = bool(done_val)
    reason = str(data.get("reason") or "").strip() or "no reason provided"
    return done, reason, False


async def judge_goal_with_llm(llm: Any, goal: str, last_response: str, subgoals: list[str] | None = None) -> tuple[str, str, bool]:
    if not goal.strip():
        return "skipped", "empty goal", False
    if not last_response.strip():
        return "continue", "empty response (nothing to evaluate)", False

    if subgoals:
        subgoals_text = "\n".join(f"- {s}" for s in subgoals)
        prompt = JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            subgoals=subgoals_text,
            response=_truncate(last_response, JUDGE_RESPONSE_SNIPPET_CHARS),
        )
        system = JUDGE_SYSTEM_PROMPT_WITH_SUBGOALS
    else:
        prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            response=_truncate(last_response, JUDGE_RESPONSE_SNIPPET_CHARS),
        )
        system = JUDGE_SYSTEM_PROMPT

    try:
        events = await llm.invoke(
            LLMContext(
                messages=[UserMessage(contents=[TextContent(content=prompt)])],
                system_prompt=system,
            )
        )
    except Exception as exc:
        return "continue", f"judge error: {type(exc).__name__}", False

    raw = ""
    for event in events:
        text = getattr(event, "text", None)
        if isinstance(text, TextContent):
            raw += text.content
    done, reason, parse_failed = parse_judge_response(raw)
    return ("done" if done else "continue"), reason, parse_failed


class GoalManager:
    def __init__(
        self,
        session_manager: Any,
        judge: JudgeFn | None = None,
        *,
        default_max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        self._session_manager = session_manager
        self._judge = judge
        self.default_max_turns = default_max_turns
        self._state = self._load()

    @property
    def state(self) -> GoalState | None:
        return self._state

    def is_active(self) -> bool:
        return self._state is not None and self._state.status == "active"

    def has_goal(self) -> bool:
        return self._state is not None and self._state.status in {"active", "paused"}

    def status_line(self) -> str:
        state = self._state
        if state is None or state.status == "cleared":
            return "No active goal. Set one with /goal <text>."
        turns = f"{state.turns_used}/{state.max_turns} turns"
        sub = f" [{len(state.subgoals)} criteria]" if state.subgoals else ""
        if state.status == "active":
            return f"Goal active ({turns}{sub}): {state.goal}"
        if state.status == "paused":
            reason = f" - {state.paused_reason}" if state.paused_reason else ""
            return f"Goal paused ({turns}{sub}{reason}): {state.goal}"
        if state.status == "done":
            return f"Goal done ({turns}{sub}): {state.goal}"
        return f"Goal {state.status} ({turns}{sub}): {state.goal}"

    def add_subgoal(self, criterion: str) -> GoalState | None:
        """Append an additional criterion to the active goal. Returns None if no goal is active."""
        if self._state is None or self._state.status not in {"active", "paused"}:
            return None
        self._state.subgoals.append(criterion.strip())
        self._save()
        return self._state

    def remove_subgoal(self, index_1based: int) -> str:
        """Remove a subgoal by 1-based index. Returns the removed text. Raises RuntimeError if no goal, IndexError if out of range."""
        if self._state is None or self._state.status not in {"active", "paused"}:
            raise RuntimeError("no active goal")
        idx = int(index_1based) - 1
        if idx < 0 or idx >= len(self._state.subgoals):
            raise IndexError(f"index out of range (1..{len(self._state.subgoals)})")
        removed = self._state.subgoals.pop(idx)
        self._save()
        return removed

    def clear_subgoals(self) -> int:
        """Remove all subgoals. Returns the previous count. Raises RuntimeError if no goal."""
        if self._state is None or self._state.status not in {"active", "paused"}:
            raise RuntimeError("no active goal")
        count = len(self._state.subgoals)
        self._state.subgoals = []
        self._save()
        return count

    def set(self, goal: str, *, max_turns: int | None = None) -> GoalState:
        goal = goal.strip()
        if not goal:
            raise ValueError("goal text is empty")
        state = GoalState(
            goal=goal,
            status="active",
            turns_used=0,
            max_turns=max_turns or self.default_max_turns,
            created_at=time.time(),
        )
        self._state = state
        self._save()
        return state

    def pause(self, reason: str = "user-paused") -> GoalState | None:
        if self._state is None:
            return None
        self._state.status = "paused"
        self._state.paused_reason = reason
        self._save()
        return self._state

    def resume(self, *, reset_budget: bool = True) -> GoalState | None:
        if self._state is None:
            return None
        self._state.status = "active"
        self._state.paused_reason = None
        if reset_budget:
            self._state.turns_used = 0
        self._save()
        return self._state

    def clear(self) -> None:
        if self._state is None:
            return
        self._state.status = "cleared"
        self._save()
        self._state = None

    async def evaluate_after_turn(self, last_response: str) -> dict[str, Any]:
        state = self._state
        if state is None or state.status != "active":
            return {
                "status": state.status if state else None,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "inactive",
                "reason": "no active goal",
                "message": "",
            }

        state.turns_used += 1
        state.last_turn_at = time.time()
        verdict, reason, parse_failed = await self._judge_result(state.goal, last_response)
        state.last_verdict = verdict
        state.last_reason = reason
        state.consecutive_parse_failures = (
            state.consecutive_parse_failures + 1 if parse_failed else 0
        )

        if verdict == "done":
            state.status = "done"
            self._save()
            return {
                "status": "done",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": verdict,
                "reason": reason,
                "message": f"Goal achieved: {reason}",
            }

        if state.consecutive_parse_failures >= DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES:
            state.status = "paused"
            state.paused_reason = (
                f"judge returned unparseable output {state.consecutive_parse_failures} turns in a row"
            )
            self._save()
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": f"Goal paused: {state.paused_reason}",
            }

        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = f"turn budget exhausted ({state.turns_used}/{state.max_turns})"
            self._save()
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"Goal paused - {state.turns_used}/{state.max_turns} turns used. "
                    "Use /goal resume to keep going, or /goal clear to stop."
                ),
            }

        self._save()
        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": self.next_continuation_prompt(),
            "verdict": "continue",
            "reason": reason,
            "message": f"Continuing toward goal ({state.turns_used}/{state.max_turns}): {reason}",
        }

    def next_continuation_prompt(self) -> str | None:
        if self._state is None or self._state.status != "active":
            return None
        if self._state.subgoals:
            subgoals_text = "\n".join(f"- {s}" for s in self._state.subgoals)
            return CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
                goal=self._state.goal,
                subgoals=subgoals_text,
            )
        return CONTINUATION_PROMPT_TEMPLATE.format(goal=self._state.goal)

    async def _judge_result(self, goal: str, last_response: str) -> tuple[str, str, bool]:
        if self._judge is None:
            return "continue", "judge unavailable", False
        subgoals = self._state.subgoals if self._state else []
        try:
            return await self._judge(goal, last_response, subgoals or None)
        except Exception as exc:
            return "continue", f"judge error: {type(exc).__name__}", False

    def _load(self) -> GoalState | None:
        for entry in reversed(self._session_manager.get_branch()):
            if not isinstance(entry, CustomInfoEntry) or entry.custom_type != GOAL_CUSTOM_TYPE:
                continue
            data = entry.data
            if not isinstance(data, dict):
                continue
            try:
                state = GoalState.from_dict(data)
            except Exception:
                return None
            if state.status == "cleared":
                return None
            return state
        return None

    def _save(self) -> None:
        if self._state is None:
            return
        self._session_manager.append_custom_info(GOAL_CUSTOM_TYPE, self._state.to_dict())
