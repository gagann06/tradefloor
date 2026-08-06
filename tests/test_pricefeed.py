import json

import pytest

import statistics

from order_book.pricefeed import (
    PriceFeed, available_feeds, downsample, load_feed, load_random_feed, rescale,
)


def write_feed(directory, slug, prices, meta=None):
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / f"{slug}.csv"
    csv_path.write_text(
        "timestamp,price\n" + "\n".join(f"{i * 1000},{p}" for i, p in enumerate(prices)),
        encoding="utf-8",
    )
    (directory / f"{slug}.json").write_text(
        json.dumps({"symbol": "TEST", "source": "unit", "interval": "1s",
                    "day": "2026-01-01", "low": min(prices), "high": max(prices),
                    **(meta or {})}),
        encoding="utf-8",
    )
    return csv_path


# --------------------------- rescaling ---------------------------

def test_rescale_preserves_shape():
    """A linear map keeps every swing proportional to every other swing."""
    scaled = rescale([10.0, 12.0, 11.0, 14.0, 10.0])

    # the 10->14 move is twice the 10->12 move, before and after
    assert (scaled[3] - scaled[0]) == pytest.approx(2 * (scaled[1] - scaled[0]), abs=1)


def test_rescale_targets_a_typical_move_of_about_a_tick():
    """The whole point: scale by volatility so the market is not frozen.

    A quiet series inside a wide range is exactly the case that a range-based
    scale destroys — every step would round onto the same tick.
    """
    raw = [1000.0]
    for i in range(400):
        raw.append(raw[-1] + (0.6 if i % 2 else -0.4))     # small, steady moves

    scaled = rescale(raw, tick_move=0.5)
    moves = [abs(scaled[i] - scaled[i - 1]) for i in range(1, len(scaled))]

    assert statistics.mean(moves) == pytest.approx(0.5, abs=0.25)


def test_a_quiet_series_still_moves():
    """Regression: a session whose range is small relative to its price used to
    flatten to a single tick."""
    raw = [65000.0 + (i % 7) * 0.8 for i in range(500)]

    scaled = rescale(raw)

    assert len(set(scaled)) > 1


def test_rescale_handles_a_flat_series():
    """A day that never moved must not divide by zero."""
    assert len(set(rescale([50.0, 50.0, 50.0]))) == 1


def test_rescale_never_produces_an_unusable_price():
    """Order rejects price <= 0, so the floor matters."""
    assert all(p >= 2 for p in rescale([1.0, 1000.0, 2.0, 900.0]))


def test_rescale_returns_integers():
    assert all(isinstance(p, int) for p in rescale([1.11, 2.22, 3.33, 2.0]))


def test_downsample_thins_a_long_series():
    assert len(downsample(list(range(10_000)), target=1_000)) <= 1_100


def test_downsample_leaves_a_short_series_alone():
    raw = [float(i) for i in range(50)]

    assert downsample(raw, target=1_000) == raw


# --------------------------- loading ---------------------------

def test_load_feed_reads_prices_and_metadata(tmp_path):
    path = write_feed(tmp_path, "unit-TEST-day", [10.0, 20.0, 30.0])

    feed = load_feed(path)

    assert len(feed) == 3
    assert feed.raw_prices == [10.0, 20.0, 30.0]
    assert feed.meta["raw_points"] == 3
    assert feed.meta["symbol"] == "TEST"
    assert feed.meta["slug"] == "unit-TEST-day"


def test_a_feed_needs_at_least_two_points():
    with pytest.raises(ValueError):
        PriceFeed([100], {})


def test_at_clamps_rather_than_running_off_the_end(tmp_path):
    feed = load_feed(write_feed(tmp_path, "unit-TEST-day", [10.0, 20.0, 30.0]))

    assert feed.at(-5) == feed.prices[0]
    assert feed.at(999) == feed.prices[-1]


# --------------------------- anonymity ---------------------------

def test_describe_hides_the_instrument_by_default(tmp_path):
    feed = load_feed(write_feed(tmp_path, "unit-TEST-day", [10.0, 20.0, 30.0]))

    described = feed.describe()

    assert described["revealed"] is False
    assert "symbol" not in described
    assert "source" not in described
    assert described["points"] == 3       # shape is fine to show, identity is not


def test_describe_reveals_on_request(tmp_path):
    feed = load_feed(write_feed(tmp_path, "unit-TEST-day", [10.0, 20.0, 30.0]))

    described = feed.describe(reveal=True)

    assert described["revealed"] is True
    assert described["symbol"] == "TEST"
    assert described["source"] == "unit"


# --------------------------- discovery ---------------------------

def test_available_feeds_is_empty_when_nothing_downloaded(tmp_path):
    assert available_feeds(tmp_path / "nothing-here") == []


def test_load_random_feed_returns_none_with_no_data(tmp_path):
    assert load_random_feed(tmp_path / "nothing-here") is None


def test_load_random_feed_picks_from_what_is_there(tmp_path):
    write_feed(tmp_path, "unit-AAA-day", [1.0, 2.0])
    write_feed(tmp_path, "unit-BBB-day", [3.0, 4.0])

    chosen = {load_random_feed(tmp_path).meta["slug"] for _ in range(40)}

    assert chosen == {"unit-AAA-day", "unit-BBB-day"}
