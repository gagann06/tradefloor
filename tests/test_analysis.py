import pytest

from order_book.analysis import SIDE_LOOKUP, round_trips
from order_book.position import Position


def fill(timestamp, side, price, quantity):
    return {"timestamp": timestamp, "side": side, "price": price, "quantity": quantity}


def test_no_fills_means_no_trips():
    assert round_trips([]) == []


def test_a_position_still_open_is_not_a_trip():
    """A trip is only complete once you are flat again."""
    assert round_trips([fill(1, "buy", 100, 10)]) == []


def test_simple_long_round_trip():
    trips = round_trips([fill(1, "buy", 100, 10), fill(2, "sell", 105, 10)])

    assert len(trips) == 1
    assert trips[0] == {
        "opened_at": 1,
        "closed_at": 2,
        "direction": "long",
        "quantity": 10,
        "entry_price": 100.0,
        "exit_price": 105.0,
        "pnl": 50.0,
        "fills": 2,
    }


def test_simple_short_round_trip():
    trips = round_trips([fill(1, "sell", 100, 10), fill(2, "buy", 90, 10)])

    assert trips[0]["direction"] == "short"
    assert trips[0]["entry_price"] == 100.0
    assert trips[0]["exit_price"] == 90.0
    assert trips[0]["pnl"] == 100.0


def test_prices_are_size_weighted_not_averaged():
    """10 @ 100 and 5 @ 101 is 100.33, not 100.5."""
    trips = round_trips([
        fill(1, "buy", 100, 10),
        fill(2, "buy", 101, 5),
        fill(3, "sell", 103, 8),
        fill(4, "sell", 104, 7),
    ])

    assert trips[0]["entry_price"] == pytest.approx(100.3333, abs=1e-4)
    assert trips[0]["exit_price"] == pytest.approx(103.4667, abs=1e-4)
    assert trips[0]["quantity"] == 15
    assert trips[0]["fills"] == 4
    assert trips[0]["pnl"] == pytest.approx(47.0)


def test_consecutive_trips_do_not_leak_into_each_other():
    """The accumulators must be reset when a trip closes."""
    trips = round_trips([
        fill(1, "buy", 100, 10), fill(2, "sell", 105, 10),
        fill(3, "sell", 200, 4), fill(4, "buy", 190, 4),
    ])

    assert [t["quantity"] for t in trips] == [10, 4]
    assert [t["entry_price"] for t in trips] == [100.0, 200.0]
    assert [t["fills"] for t in trips] == [2, 2]


# --------------------------- flips ---------------------------

def test_flip_long_to_short_splits_the_fill():
    """sell 15 against a long 10 closes the long AND opens a short 5."""
    trips = round_trips([
        fill(1, "buy", 100, 10),
        fill(2, "sell", 110, 15),
        fill(3, "buy", 108, 5),
    ])

    assert len(trips) == 2

    closed, opened = trips
    assert (closed["direction"], closed["quantity"]) == ("long", 10)
    assert closed["exit_price"] == 110.0          # only the closing 10
    assert closed["pnl"] == 100.0

    assert (opened["direction"], opened["quantity"]) == ("short", 5)
    assert opened["entry_price"] == 110.0         # only the opening 5
    assert opened["opened_at"] == 2               # the same fill that closed the first
    assert opened["pnl"] == 10.0


def test_flip_short_to_long():
    trips = round_trips([
        fill(1, "sell", 100, 8),
        fill(2, "buy", 95, 20),
        fill(3, "sell", 99, 12),
    ])

    assert [(t["direction"], t["quantity"], t["pnl"]) for t in trips] == [
        ("short", 8, 40.0),
        ("long", 12, 48.0),
    ]


def test_repeated_flips():
    trips = round_trips([
        fill(1, "buy", 100, 5),
        fill(2, "sell", 110, 10),
        fill(3, "buy", 105, 10),
        fill(4, "sell", 107, 5),
    ])

    assert [t["direction"] for t in trips] == ["long", "short", "long"]
    assert [t["pnl"] for t in trips] == [50.0, 25.0, 10.0]


def test_a_flip_does_not_double_count_pnl():
    """pnl_at_open must be read after the closing part is realised."""
    trips = round_trips([
        fill(1, "buy", 100, 10),
        fill(2, "sell", 110, 15),
        fill(3, "buy", 108, 5),
    ])

    assert sum(t["pnl"] for t in trips) == 110.0     # 100 + 10, not 100 + 110


# --------------------------- agreement with Position ---------------------------

@pytest.mark.parametrize("fills", [
    [fill(1, "buy", 100, 10), fill(2, "sell", 105, 10)],
    [fill(1, "sell", 100, 8), fill(2, "buy", 95, 20), fill(3, "sell", 99, 12)],
    [fill(1, "buy", 100, 10), fill(2, "buy", 101, 5),
     fill(3, "sell", 103, 8), fill(4, "sell", 104, 7)],
    [fill(1, "buy", 100, 5), fill(2, "sell", 110, 10),
     fill(3, "buy", 105, 10), fill(4, "sell", 107, 5)],
])
def test_trip_pnl_sums_to_the_position_it_came_from(fills):
    """The journal and the P&L panel must never disagree about the same money.

    They cannot, because the trips are derived from the very same Position
    class the panel uses — this test is what proves that stays true.
    """
    position = Position()
    for f in fills:
        position.apply_fill(SIDE_LOOKUP[f["side"]], f["price"], f["quantity"])

    total = sum(t["pnl"] for t in round_trips(fills))

    assert total == pytest.approx(position.realised_pnl)


def test_open_position_is_excluded_from_the_total():
    """Unrealised money belongs to no completed trip."""
    fills = [
        fill(1, "buy", 100, 10), fill(2, "sell", 105, 10),   # closed, +50
        fill(3, "buy", 200, 4),                              # still open
    ]

    trips = round_trips(fills)

    assert len(trips) == 1
    assert sum(t["pnl"] for t in trips) == 50.0
