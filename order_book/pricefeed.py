"""Replayable price series loaded from real market data.

The engine works in integer prices around a small anchor; real instruments trade
at 770 or 65,000 with their own tick sizes. A feed therefore gets rescaled onto
the engine's price grid before replay.

The rescaling is a straight linear map, which preserves the *shape* of the path
exactly — every swing keeps its proportion relative to every other swing.

Crucially it scales by **volatility, not by range**. Fitting a whole session
into a fixed number of ticks sounds reasonable and is useless in practice: a day
of Bitcoin ranges 1.7%, so forty ticks makes one tick worth $28 while a typical
one-second move is $0.84 — and 97% of consecutive points then land on the same
tick. Scaling so a *typical move* is about half a tick keeps the market alive,
and lets the session range be however wide it needs to be; the ladder only ever
shows a window of it anyway.

Feeds are also downsampled toward a target point count. At one-second
resolution a real market genuinely does not move most seconds, which is honest
but makes for a frozen replay. Coarser steps carry more movement each.
"""

import csv
import json
import pathlib
import statistics

DEFAULT_ANCHOR = 100
TARGET_POINTS = 5_000    # downsample toward this, so a feed replays in minutes
TICK_MOVE = 0.5          # a typical step should be about this many ticks
FLOOR = 50               # lowest engine price, keeping everything comfortably > 0


class PriceFeed:
    """A rescaled series plus the metadata describing where it came from."""

    def __init__(self, prices, meta, raw_prices=None):
        if len(prices) < 2:
            raise ValueError("a feed needs at least two prices")
        self.prices = prices
        self.meta = meta
        self.raw_prices = raw_prices or []

    def __len__(self):
        return len(self.prices)

    def at(self, index):
        """Price at a position, clamped so a caller cannot run off the end."""
        if index < 0:
            index = 0
        elif index >= len(self.prices):
            index = len(self.prices) - 1
        return self.prices[index]

    def describe(self, reveal=False):
        """Feed metadata. Withholds the instrument unless asked.

        Knowing you are looking at TSLA imports assumptions about how it ought
        to behave, so a session trades an unlabelled series and the identity is
        revealed afterwards.
        """
        described = {
            "points": len(self.prices),
            "low": min(self.prices),
            "high": max(self.prices),
            "first": self.prices[0],
            "last": self.prices[-1],
            "revealed": bool(reveal),
        }
        if reveal:
            described.update({
                "symbol": self.meta.get("symbol"),
                "source": self.meta.get("source"),
                "interval": self.meta.get("interval"),
                "day": self.meta.get("day") or self.meta.get("range"),
                "real_low": self.meta.get("low"),
                "real_high": self.meta.get("high"),
            })
        return described


def downsample(raw, target=TARGET_POINTS):
    """Keep every Nth point so each replay step carries real movement."""
    if target <= 0 or len(raw) <= target:
        return raw
    return raw[:: max(1, len(raw) // target)]


def rescale(raw, tick_move=TICK_MOVE, floor=FLOOR):
    """Map real prices onto the engine's integer grid, scaled by volatility."""
    moves = [abs(raw[i] - raw[i - 1]) for i in range(1, len(raw))]
    active = [m for m in moves if m > 0]
    if not active:
        return [DEFAULT_ANCHOR] * len(raw)

    # one typical move should be worth roughly `tick_move` ticks
    scale = tick_move / statistics.mean(active)
    scaled = [p * scale for p in raw]

    # shift rather than centre: the range can be hundreds of ticks wide, so
    # centring on a small anchor would push the low end below zero
    shift = floor - min(scaled)
    return [max(2, round(p + shift)) for p in scaled]


def load_feed(csv_path, target=TARGET_POINTS, tick_move=TICK_MOVE):
    csv_path = pathlib.Path(csv_path)
    raw = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw.append(float(row["price"]))

    meta_path = csv_path.with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["slug"] = csv_path.stem
    meta["raw_points"] = len(raw)

    sampled = downsample(raw, target)
    return PriceFeed(rescale(sampled, tick_move), meta, raw_prices=sampled)


def available_feeds(directory):
    directory = pathlib.Path(directory)
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.csv"))


def load_random_feed(directory, target=TARGET_POINTS, rng=None):
    """Pick a feed at random. Returns None when none have been downloaded."""
    import random as _random

    feeds = available_feeds(directory)
    if not feeds:
        return None
    chooser = rng or _random
    return load_feed(chooser.choice(feeds), target)
