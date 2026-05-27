"""LCM — Lossless Context Management compaction strategy.

Instead of discarding old turns, LCM:
  1. Persists every archived message to a SQLite store.
  2. Summarizes them into a depth-0 (leaf) DAG node.
  3. Condenses leaf nodes into higher-depth nodes as they accumulate.
  4. Injects the top-level summary into the active context window.
  5. Exposes `lcm_grep` and `lcm_expand` tools so the agent can
     retrieve any archived detail on demand.

Nothing is ever truly lost — the agent can always search back.

Based on the LCM paper by Ehrlich & Blackman (Voltropy PBC, 2026)
and the hermes-lcm plugin by stephenschoettler.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from operator_use.inference.types import LLMContext, ThinkingLevel
from operator_use.message.types import UserMessage, TextContent
from operator_use.session.types import SessionEntry, CompactionEntry

from operator_use.compaction.strategy.base import Compaction
from operator_use.compaction.strategy.types import (
    CompactionSettings, CompactionPreparation, CompactionResult, CompactionDetails,
)
from operator_use.compaction.strategy.lcm.types import LCMSettings, LCMNode
from operator_use.compaction.strategy.lcm.store import MessageStore
from operator_use.compaction.strategy.lcm.dag import SummaryDAG
from operator_use.compaction.strategy.lcm.prompts import (
    LEAF_SUMMARY_SYSTEM_PROMPT, LEAF_SUMMARY_PROMPT,
    CONDENSE_PROMPT, ACTIVE_CONTEXT_PREAMBLE,
)
from operator_use.compaction.strategy.utils import (
    estimate_context_tokens, collect_messages_in_range,
    find_prev_compaction_index, resolve_boundary_start, find_cut_point,
    build_file_ops_from_prev_compaction, accumulate_file_ops,
    compute_file_lists, format_file_operations,
    serialize_conversation, extract_text_from_events,
)

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM
    from operator_use.tool.types import Tool


_DEFAULT_DB_NAME = "lcm.db"


class LCMCompaction(Compaction):
    def __init__(
        self,
        llm: LLM,
        settings: LCMSettings | None = None,
        settings_provider: Callable[[], LCMSettings] | None = None,
        session_id_provider: Callable[[], str] | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.llm = llm
        self.settings = settings or LCMSettings()
        self._settings_provider = settings_provider
        self._session_id_provider = session_id_provider

        resolved_db = db_path or (self.settings.db_path) or self._default_db_path()
        self._store = MessageStore(resolved_db)
        self._dag = SummaryDAG(resolved_db)

    @staticmethod
    def _default_db_path() -> Path:
        from pathlib import Path as _Path
        home = _Path.home()
        db_dir = home / ".operator"
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / _DEFAULT_DB_NAME

    @property
    def _settings(self) -> LCMSettings:
        if self._settings_provider is not None:
            return self._settings_provider()
        return self.settings

    def _get_session_id(self) -> str:
        if self._session_id_provider is not None:
            sid = self._session_id_provider()
            if sid:
                return sid
        return "default"

    # -------------------------------------------------------------------------
    # Compaction ABC
    # -------------------------------------------------------------------------

    def should_compact(self, context_tokens: int, context_window: int) -> bool:
        settings = self._settings
        if not settings.enabled:
            return False
        return context_tokens > context_window - settings.reserve_tokens

    def prepare(self, path_entries: list[SessionEntry]) -> CompactionPreparation | None:
        settings = self._settings

        if path_entries and isinstance(path_entries[-1], CompactionEntry):
            return None

        prev_idx = find_prev_compaction_index(path_entries)
        previous_summary = path_entries[prev_idx].summary if prev_idx >= 0 else None  # type: ignore[union-attr]
        boundary_start = resolve_boundary_start(path_entries, prev_idx)
        boundary_end = len(path_entries)

        tokens_before = estimate_context_tokens(
            collect_messages_in_range(path_entries, 0, boundary_end)
        ).tokens

        cut = find_cut_point(path_entries, boundary_start, boundary_end, settings.keep_recent_tokens)
        if cut.first_kept_entry_index >= boundary_end:
            return None
        first_kept_entry = path_entries[cut.first_kept_entry_index]
        if not first_kept_entry.id:
            return None

        history_end = cut.turn_start_index if cut.is_split_turn else cut.first_kept_entry_index
        messages_to_summarize = collect_messages_in_range(
            path_entries, boundary_start, history_end, for_compaction=True
        )
        turn_prefix_messages = (
            collect_messages_in_range(
                path_entries, cut.turn_start_index, cut.first_kept_entry_index,
                for_compaction=True,
            )
            if cut.is_split_turn else []
        )

        file_ops = build_file_ops_from_prev_compaction(path_entries, prev_idx)
        accumulate_file_ops(file_ops, messages_to_summarize)
        if cut.is_split_turn:
            accumulate_file_ops(file_ops, turn_prefix_messages)

        return CompactionPreparation(
            retained_from_id=first_kept_entry.id,
            messages_to_summarize=messages_to_summarize,
            turn_prefix_messages=turn_prefix_messages,
            is_split_turn=cut.is_split_turn,
            tokens_before=tokens_before,
            previous_summary=previous_summary,
            file_ops=file_ops,
            settings=CompactionSettings(
                enabled=settings.enabled,
                keep_recent_tokens=settings.keep_recent_tokens,
            ),
        )

    async def compact(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> CompactionResult:
        session_id = self._get_session_id()
        settings = self._settings
        all_messages = preparation.messages_to_summarize + preparation.turn_prefix_messages

        # 1. Persist archived messages to store
        store_ids = self._store.persist(session_id, all_messages)

        # 2. Summarize into a leaf (D0) DAG node
        leaf_summary = await self._summarize_leaf(all_messages, custom_instructions, thinking_level)

        expand_hint = f"Expand for details about {len(all_messages)} archived turns"
        d0_node = LCMNode(
            session_id=session_id,
            depth=0,
            summary=leaf_summary,
            source_ids=store_ids,
            source_type="messages",
            expand_hint=expand_hint,
            created_at=time.time(),
        )
        self._dag.add_node(d0_node)

        # 3. Condense up the DAG hierarchy as nodes accumulate
        await self._maybe_condense(session_id, depth=0, settings=settings,
                                   thinking_level=thinking_level)

        # 4. Build the active context summary
        active_summary = self._build_active_summary(session_id)

        read_files, modified_files = compute_file_lists(preparation.file_ops)
        active_summary += format_file_operations(read_files, modified_files)

        return CompactionResult(
            summary=active_summary,
            retained_from_id=preparation.retained_from_id,
            tokens_before=preparation.tokens_before,
            details=CompactionDetails(read_files=read_files, modified_files=modified_files),
        )

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    def get_tools(self) -> list[Tool]:
        """Return LCM retrieval tools to wire into the agent."""
        from operator_use.compaction.strategy.lcm.tools import LCMGrepTool, LCMExpandTool
        return [
            LCMGrepTool(self._dag, self._store, self._get_session_id),
            LCMExpandTool(self._dag, self._store),
        ]

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    async def _summarize_leaf(
        self,
        messages: list,
        custom_instructions: str | None,
        thinking_level: ThinkingLevel | None,
    ) -> str:
        conversation_text = serialize_conversation(messages)
        extra = f"\n\nAdditional focus: {custom_instructions}" if custom_instructions else ""
        prompt = f"<conversation>\n{conversation_text}\n</conversation>\n\n{LEAF_SUMMARY_PROMPT}{extra}"
        context = LLMContext(
            messages=[UserMessage(contents=[TextContent(content=prompt)])],
            system_prompt=LEAF_SUMMARY_SYSTEM_PROMPT,
        )
        events = await self.llm.invoke(context, thinking_level=thinking_level)
        return extract_text_from_events(events, "LCM leaf summarization failed")

    async def _summarize_nodes(
        self,
        nodes: list[LCMNode],
        thinking_level: ThinkingLevel | None,
    ) -> str:
        combined = "\n\n---\n\n".join(
            f"[Segment {i + 1}]\n{node.summary}" for i, node in enumerate(nodes)
        )
        prompt = f"<summaries>\n{combined}\n</summaries>\n\n{CONDENSE_PROMPT}"
        context = LLMContext(
            messages=[UserMessage(contents=[TextContent(content=prompt)])],
            system_prompt=LEAF_SUMMARY_SYSTEM_PROMPT,
        )
        events = await self.llm.invoke(context, thinking_level=thinking_level)
        return extract_text_from_events(events, "LCM condensation failed")

    async def _maybe_condense(
        self,
        session_id: str,
        depth: int,
        settings: LCMSettings,
        thinking_level: ThinkingLevel | None,
    ) -> None:
        if depth >= settings.max_depth - 1:
            return
        uncondensed = self._dag.get_uncondensed(session_id, depth)
        if len(uncondensed) < settings.condense_threshold:
            return

        condensed_summary = await self._summarize_nodes(uncondensed, thinking_level)
        parent = LCMNode(
            session_id=session_id,
            depth=depth + 1,
            summary=condensed_summary,
            source_ids=[n.node_id for n in uncondensed],
            source_type="nodes",
            expand_hint=f"Expand for {len(uncondensed)} earlier summaries",
            created_at=time.time(),
        )
        self._dag.add_node(parent)
        # Recurse: maybe the new depth can condense further
        await self._maybe_condense(session_id, depth + 1, settings, thinking_level)

    def _build_active_summary(self, session_id: str) -> str:
        top = self._dag.get_top_level(session_id)
        if not top:
            return ACTIVE_CONTEXT_PREAMBLE + "(No archived context yet.)"

        depth_label = f"D{top.depth} summary"
        return (
            f"{ACTIVE_CONTEXT_PREAMBLE}"
            f"**Latest archived {depth_label} (node_id={top.node_id}):**\n\n"
            f"{top.summary}\n\n"
            f"Use `lcm_grep` to search or `lcm_expand` with node_id to drill deeper."
        )
