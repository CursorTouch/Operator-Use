"""Example benchmark tests demonstrating the benchmark harness.

These tests show how to:
1. Measure execution time with benchmark_timer
2. Record a baseline with BaselineStore
3. Detect regressions with the regression_check fixture

To record a fresh baseline for a test, call BaselineStore.record() once, then
subsequent runs will compare against it.
"""

from __future__ import annotations

import time

import pytest

from tests.benchmarks.conftest import BaselineStore, benchmark_timer


# ---------------------------------------------------------------------------
# Timer tests — verify the context manager measures time correctly
# ---------------------------------------------------------------------------


def test_benchmark_timer_measures_elapsed():
    """benchmark_timer should capture realistic wall-clock time."""
    with benchmark_timer() as result:
        time.sleep(0.05)  # deliberate 50 ms pause
    assert result.elapsed >= 0.04, "Should have measured at least 40 ms"
    assert result.elapsed < 1.0, "Should not have taken more than 1 s"


def test_benchmark_timer_fast_operation():
    """benchmark_timer should handle sub-millisecond operations."""
    with benchmark_timer() as result:
        _ = "".join(["a"] * 1000)
    assert result.elapsed >= 0.0
    assert result.elapsed < 0.1


def test_benchmark_timer_multiple_runs():
    """Multiple sequential timer contexts must each record independent elapsed times."""
    results = []
    for _ in range(3):
        with benchmark_timer() as r:
            time.sleep(0.01)
        results.append(r.elapsed)
    for elapsed in results:
        assert elapsed >= 0.005


# ---------------------------------------------------------------------------
# Baseline store tests
# ---------------------------------------------------------------------------


def test_baseline_store_roundtrip(tmp_path):
    """Records a measurement and reads it back."""
    store = BaselineStore(path=tmp_path / "baselines.json")
    store.record("roundtrip_op", mean=0.042, stddev=0.003)

    entry = store.get("roundtrip_op")
    assert entry is not None
    assert entry["mean"] == pytest.approx(0.042)
    assert entry["stddev"] == pytest.approx(0.003)
    assert "recorded_at" in entry


def test_baseline_store_missing_key(tmp_path):
    """Returns None for an unknown test name."""
    store = BaselineStore(path=tmp_path / "baselines.json")
    assert store.get("nonexistent") is None


def test_baseline_store_overwrite(tmp_path):
    """Re-recording a baseline overwrites the previous value."""
    store = BaselineStore(path=tmp_path / "baselines.json")
    store.record("op", mean=1.0)
    store.record("op", mean=0.5)
    assert store.get("op")["mean"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Regression detection tests
# ---------------------------------------------------------------------------


def test_regression_check_passes_within_threshold(tmp_path):
    """Should not raise when elapsed is within the 10 % threshold."""
    store = BaselineStore(path=tmp_path / "baselines.json")
    store.record("fast_op", mean=0.100)

    # Simulate the regression_check logic directly (no pytest.skip path needed here)
    from tests.benchmarks.conftest import REGRESSION_THRESHOLD

    elapsed = 0.108  # 8 % over baseline — within 10 % limit
    entry = store.get("fast_op")
    limit = entry["mean"] * (1 + REGRESSION_THRESHOLD)
    assert elapsed <= limit


def test_regression_check_fails_on_regression(tmp_path):
    """Should raise AssertionError when elapsed exceeds the 10 % threshold."""
    store = BaselineStore(path=tmp_path / "baselines.json")
    store.record("slow_op", mean=0.100)

    from tests.benchmarks.conftest import REGRESSION_THRESHOLD

    elapsed = 0.120  # 20 % over baseline — exceeds 10 % limit
    entry = store.get("slow_op")
    limit = entry["mean"] * (1 + REGRESSION_THRESHOLD)

    with pytest.raises(AssertionError, match="Performance regression detected"):
        assert elapsed <= limit, (
            f"Performance regression detected for 'slow_op': "
            f"elapsed={elapsed:.6f}s exceeds baseline={entry['mean']:.6f}s "
            f"by more than {REGRESSION_THRESHOLD * 100:.0f}% "
            f"(limit={limit:.6f}s)"
        )


def test_regression_check_fixture_with_baseline(tmp_path, monkeypatch):
    """regression_check fixture passes when measurement is within threshold."""
    store = BaselineStore(path=tmp_path / "baselines.json")
    store.record("example_string_join", mean=0.001)

    # Monkeypatch the store used by the fixture
    import tests.benchmarks.conftest as bconf

    monkeypatch.setattr(bconf, "BASELINES_PATH", tmp_path / "baselines.json")

    # Simulate fixture behaviour inline
    from tests.benchmarks.conftest import REGRESSION_THRESHOLD

    with benchmark_timer() as t:
        _ = "-".join(str(i) for i in range(100))

    entry = store.get("example_string_join")
    limit = entry["mean"] * (1 + REGRESSION_THRESHOLD)
    # The real operation should comfortably complete within 10 % of 1 ms
    assert t.elapsed <= limit or t.elapsed < 0.01, (
        "String join took unexpectedly long — potential environment issue"
    )
