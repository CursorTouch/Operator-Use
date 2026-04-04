#!/usr/bin/env python
"""Example: Using DAG dependencies, retry, and tracing together."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from operator_use.subagent.manager import SubagentManager
from operator_use.config import SubagentConfig, RetryConfig
from operator_use.tracing import Tracer, TraceEvent
from operator_use.bus import Bus

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Example: orchestrate multiple subagents with dependencies and tracing."""

    # Configure retry: 2 retries with exponential backoff
    retry_config = RetryConfig(
        max_retries=2,
        base_delay=0.5,
        max_delay=10.0,
        backoff_factor=2.0,
    )

    # Create subagent config with retry enabled
    subagent_config = SubagentConfig(
        max_iterations=20,
        retry=retry_config,
    )

    # Create a tracer that logs all operations
    trace_events = []

    async def on_trace(event: TraceEvent):
        """Callback that receives all trace events."""
        trace_events.append(event)
        logger.info(
            f"TRACE | {event.event_type:15} | {event.span_id:10} | "
            f"duration={event.duration_ms:.1f if event.duration_ms else 'N/A'}ms"
        )

    tracer = Tracer(agent_id="orchestrator", on_trace=on_trace)

    # Create bus and manager (you'd normally get these from Agent)
    bus = Bus()  # In real usage, this comes from Orchestrator
    mock_llm = None  # In real usage, this is an actual LLM provider

    manager = SubagentManager(
        llm=mock_llm,
        bus=bus,
        config=subagent_config,
        tracer=tracer,
    )

    logger.info("=" * 70)
    logger.info("ORCHESTRATION EXAMPLE: DAG + Retry + Tracing")
    logger.info("=" * 70)

    # ──────────────────────────────────────────────────────────────────────
    # Scenario: Multi-stage data pipeline with dependencies
    # ──────────────────────────────────────────────────────────────────────

    logger.info("\n1. Creating independent tasks (no dependencies)")
    logger.info("-" * 70)

    # Stage 1: Fetch raw data (independent tasks)
    data_fetch_1 = await manager.ainvoke(
        task="Fetch weather data for Q1 2026 from OpenWeatherMap API",
        label="Weather Data Fetch (Q1)",
        channel="slack",
        chat_id="C123456",
    )
    logger.info(f"   Created task: {data_fetch_1}")

    data_fetch_2 = await manager.ainvoke(
        task="Fetch historical stock prices for AAPL, MSFT, GOOGL",
        label="Stock Price Fetch",
        channel="slack",
        chat_id="C123456",
    )
    logger.info(f"   Created task: {data_fetch_2}")

    logger.info("\n2. Creating dependent tasks (wait for stage 1)")
    logger.info("-" * 70)

    # Stage 2: Process the data (depends on both stage 1 tasks)
    data_process = await manager.ainvoke(
        task="Normalize and clean the fetched weather and stock data; remove outliers",
        label="Data Processing",
        channel="slack",
        chat_id="C123456",
        depends_on=[data_fetch_1, data_fetch_2],  # ← DAG dependency
    )
    logger.info(f"   Created task (depends_on=[{data_fetch_1}, {data_fetch_2}]): {data_process}")

    logger.info("\n3. Creating analysis task (wait for stage 2)")
    logger.info("-" * 70)

    # Stage 3: Analyze the processed data (depends on stage 2)
    analysis = await manager.ainvoke(
        task="Analyze correlation between weather patterns and stock price movements; identify trends",
        label="Correlation Analysis",
        channel="slack",
        chat_id="C123456",
        depends_on=[data_process],  # ← DAG dependency
    )
    logger.info(f"   Created task (depends_on=[{data_process}]): {analysis}")

    logger.info("\n4. Creating reporting task (wait for stage 3)")
    logger.info("-" * 70)

    # Stage 4: Generate final report (depends on stage 3)
    report = await manager.ainvoke(
        task="Create executive summary report with visualizations, insights, and recommendations",
        label="Report Generation",
        channel="slack",
        chat_id="C123456",
        depends_on=[analysis],  # ← DAG dependency
    )
    logger.info(f"   Created task (depends_on=[{analysis}]): {report}")

    logger.info("\n" + "=" * 70)
    logger.info("Dependency graph:")
    logger.info(f"  fetch_1 ──┐")
    logger.info(f"            ├──→ process ──→ analysis ──→ report")
    logger.info(f"  fetch_2 ──┘")
    logger.info("=" * 70)

    # ──────────────────────────────────────────────────────────────────────
    # Show configuration summary
    # ──────────────────────────────────────────────────────────────────────

    logger.info("\n\nCONFIGURATION SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Retry enabled:           {subagent_config.retry is not None}")
    if subagent_config.retry:
        logger.info(f"  Max retries:           {subagent_config.retry.max_retries}")
        logger.info(f"  Base delay:            {subagent_config.retry.base_delay}s")
        logger.info(f"  Max delay:             {subagent_config.retry.max_delay}s")
        logger.info(f"  Backoff factor:        {subagent_config.retry.backoff_factor}x")
    logger.info(f"Tracer enabled:          {tracer is not None}")
    logger.info(f"  Agent ID:              {tracer.agent_id if tracer else 'N/A'}")

    # ──────────────────────────────────────────────────────────────────────
    # Show execution plan
    # ──────────────────────────────────────────────────────────────────────

    logger.info("\n\nEXECUTION PLAN")
    logger.info("=" * 70)

    all_records = manager.list_all()
    for record in all_records:
        logger.info(f"\nTask ID: {record.task_id}")
        logger.info(f"  Label:       {record.label}")
        logger.info(f"  Status:      {record.status}")
        if record.depends_on:
            logger.info(f"  Depends on:  {', '.join(record.depends_on)}")
        if record.dependents:
            logger.info(f"  Waiting for: {', '.join(record.dependents)}")
        logger.info(f"  Started:     {record.started_at.isoformat()}")

    # ──────────────────────────────────────────────────────────────────────
    # Show how features work together
    # ──────────────────────────────────────────────────────────────────────

    logger.info("\n\nFEATURES IN ACTION")
    logger.info("=" * 70)

    logger.info("""
✅ DAG Dependencies:
   • fetch_1 and fetch_2 run in parallel (no deps)
   • process waits for both fetch tasks to complete
   • If fetch_1 fails, process is automatically cancelled (fail_fast)
   • analysis only starts after process finishes
   • report is the final output

✅ Exponential Backoff Retry:
   • Each task can fail up to 2 times + 1 initial = 3 attempts total
   • First retry: wait 0.5s
   • Second retry: wait 1.0s (0.5s × 2)
   • If still failing: max 10s delay cap applies
   • On CancelledError: immediate exit (no retry)

✅ Unified Observability Tracing:
   • All LLM calls emit TraceEvent with token counts
   • All tool calls emit TraceEvent with name and result
   • All subagent runs emit TraceEvent with task_id and status
   • Trace events have parent-child relationships (spans)
   • Zero overhead if tracer=None
""")

    logger.info("\n" + "=" * 70)
    logger.info("EXAMPLE COMPLETE")
    logger.info(
        f"Created {len(all_records)} tasks with {sum(1 for r in all_records if r.depends_on)} dependencies"
    )
    logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
