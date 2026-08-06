"""Smoke tests for the benchmark harness.

Not performance assertions — timings vary far too much by machine to gate a test
suite on. These only check the harness still runs and reports sane shapes, so it
cannot rot silently as the engine changes.
"""

import importlib.util
import pathlib
import random

import pytest

BENCHMARK_PATH = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "benchmark.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("benchmark", BENCHMARK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rng():
    return random.Random(1234)


def test_percentiles_are_ordered(bench):
    result = bench.percentiles(list(range(1, 1001)))

    assert result["p50_us"] <= result["p95_us"] <= result["p99_us"] <= result["max_us"]


def test_summarise_reports_throughput(bench):
    row = bench.summarise("demo", 1_000_000_000, [1000] * 500)

    assert row["scenario"] == "demo"
    assert row["operations"] == 500
    assert row["ops_per_sec"] == pytest.approx(500.0)


@pytest.mark.parametrize(
    "name",
    ["bench_order_construction", "bench_resting_inserts", "bench_crossing",
     "bench_market_sweeps", "bench_cancels", "bench_mixed"],
)
def test_each_scenario_runs_and_reports(bench, rng, name):
    row = getattr(bench, name)(200, rng)

    assert row["operations"] > 0
    assert row["ops_per_sec"] > 0
    assert row["mean_us"] > 0
    assert row["p50_us"] <= row["max_us"]


def test_crossing_scenario_actually_generates_trades(bench, rng):
    """If this stops trading, the benchmark would be measuring the wrong path."""
    row = bench.bench_crossing(200, rng)

    assert row["trades_generated"] > 0


def test_market_sweep_scenario_actually_sweeps(bench, rng):
    row = bench.bench_market_sweeps(500, rng)

    # each market order is sized to consume several price levels
    assert row["trades_generated"] > row["operations"]


def test_cancel_scenario_empties_the_book(bench, rng):
    row = bench.bench_cancels(200, rng)

    assert row["orders_left"] == 0


def test_depth_scaling_returns_a_row_per_size(bench, rng):
    rows = bench.bench_depth_scaling((100, 500), 50, rng)

    assert [r["book_size"] for r in rows] == [100, 500]
    assert all(r["ops_per_sec"] > 0 for r in rows)


def test_api_benchmark_runs(bench):
    row = bench.bench_api(25)

    assert row is not None
    assert row["operations"] == 25
    assert row["ops_per_sec"] > 0
