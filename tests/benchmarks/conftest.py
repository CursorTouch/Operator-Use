"""Benchmark fixtures and regression detection for Operator-Use performance tests.

This module provides:
- benchmark_timer: context manager that records execution time
- baseline_store: accessor for the JSON baseline file
- regression_check: pytest fixture to compare current vs baseline timings
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest

BASELINES_PATH = Path(__file__).parent / "baselines.json"

# Maximum allowed regression over baseline before a check fails (10 %).
REGRESSION_THRESHOLD = 0.10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TimingResult:
    """Holds the elapsed time from a single benchmark_timer context run."""

    def __init__(self) -> None:
        self.elapsed: float = 0.0
        self._start: float = 0.0

    def __repr__(self) -> str:  # pragma: no cover
        return f"TimingResult(elapsed={self.elapsed:.6f}s)"


@contextmanager
def benchmark_timer() -> Generator[TimingResult, None, None]:
    """Context manager that records wall-clock execution time.

    Usage::

        with benchmark_timer() as result:
            do_work()
        assert result.elapsed < 1.0
    """
    result = TimingResult()
    result._start = time.perf_counter()
    try:
        yield result
    finally:
        result.elapsed = time.perf_counter() - result._start


# ---------------------------------------------------------------------------
# Baseline store
# ---------------------------------------------------------------------------


class BaselineStore:
    """Read/write access to the baselines JSON file.

    Entries are keyed by test name and contain::

        {
            "mean": <float seconds>,
            "stddev": <float seconds>,
            "recorded_at": <ISO-8601 string>
        }
    """

    def __init__(self, path: Path = BASELINES_PATH) -> None:
        self._path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            raw = self._path.read_text(encoding="utf-8")
            return json.loads(raw)
        return {}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, indent=2) + "\n",
            encoding="utf-8",
        )

    def get(self, name: str) -> dict | None:
        """Return the baseline entry for *name*, or None if absent."""
        return self._data.get(name)

    def record(self, name: str, mean: float, stddev: float = 0.0) -> None:
        """Persist a new baseline measurement for *name*."""
        self._data[name] = {
            "mean": mean,
            "stddev": stddev,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def baseline_store() -> BaselineStore:
    """Pytest fixture that provides a BaselineStore backed by baselines.json."""
    return BaselineStore()


@pytest.fixture
def regression_check(baseline_store: BaselineStore):
    """Pytest fixture for performance regression detection.

    Yields a callable ``check(name, elapsed)`` that fails the test when the
    measured *elapsed* time exceeds the stored baseline by more than
    ``REGRESSION_THRESHOLD`` (10 %).

    If no baseline exists for *name* the check is skipped and a warning is
    emitted so CI does not hard-fail on first run.

    Example::

        def test_my_op_perf(regression_check):
            with benchmark_timer() as t:
                my_operation()
            regression_check("my_op", t.elapsed)
    """

    def _check(name: str, elapsed: float) -> None:
        entry = baseline_store.get(name)
        if entry is None:
            pytest.skip(
                f"No baseline recorded for '{name}'. Run with --record-baseline to capture one."
            )
            return

        baseline_mean: float = entry["mean"]
        limit = baseline_mean * (1 + REGRESSION_THRESHOLD)
        assert elapsed <= limit, (
            f"Performance regression detected for '{name}': "
            f"elapsed={elapsed:.6f}s exceeds baseline={baseline_mean:.6f}s "
            f"by more than {REGRESSION_THRESHOLD * 100:.0f}% "
            f"(limit={limit:.6f}s)"
        )

    return _check
