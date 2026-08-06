import pytest

from order_book.analysis import SIDE_LOOKUP, excursions, round_trips, session_stats
from order_book.position import Position


def fill(timestamp, side, price, quantity):
    """A journal-shaped fill. The touch defaults to straddling the fill price so
    the execution stats see something valid; tests that care about execution use
    execution_fill and set it explicitly."""
    return {
        "timestamp": timestamp, "side": side, "price": price, "quantity": quantity,
        "best_bid": price, "best_ask": price, "aggressor": 1,
    }


def execution_fill(timestamp, side, price, quantity, best_bid, best_ask, aggressor):
    return {
        "timestamp": timestamp, "side": side, "price": price, "quantity": quantity,
        "best_bid": best_bid, "best_ask": best_ask, "aggressor": aggressor,
    }


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


# --------------------------- session stats: direction ---------------------------

def test_empty_session_reports_zeroes_without_dividing_by_zero():
    stats = session_stats([])

    assert stats["trips"] == 0
    assert stats["win_rate"] == 0.0
    assert stats["expectancy"] == 0.0
    assert stats["avg_edge_crossing"] == 0.0
    assert stats["total_spread_cost"] == 0


def test_win_rate_and_averages():
    stats = session_stats([
        fill(1, "buy", 100, 10), fill(2, "sell", 105, 10),    # +50
        fill(3, "buy", 100, 10), fill(4, "sell", 98, 10),     # -20
        fill(5, "sell", 100, 5), fill(6, "buy", 98, 5),       # +10
    ])

    assert stats["trips"] == 3
    assert (stats["wins"], stats["losses"]) == (2, 1)
    assert stats["win_rate"] == pytest.approx(2 / 3)
    assert stats["total_pnl"] == 40.0
    assert stats["average_win"] == 30.0
    assert stats["average_loss"] == -20.0
    assert stats["largest_win"] == 50.0
    assert stats["largest_loss"] == -20.0
    assert stats["expectancy"] == pytest.approx(40 / 3)


def test_a_session_with_no_losses_does_not_divide_by_zero():
    stats = session_stats([fill(1, "buy", 100, 10), fill(2, "sell", 105, 10)])

    assert stats["losses"] == 0
    assert stats["average_loss"] == 0.0
    assert stats["largest_loss"] == 0


def test_high_win_rate_can_still_lose_money():
    """Why win rate alone is not a verdict: three small wins, one big loss."""
    stats = session_stats([
        fill(1, "buy", 100, 10), fill(2, "sell", 101, 10),    # +10
        fill(3, "buy", 100, 10), fill(4, "sell", 101, 10),    # +10
        fill(5, "buy", 100, 10), fill(6, "sell", 101, 10),    # +10
        fill(7, "buy", 100, 10), fill(8, "sell", 90, 10),     # -100
    ])

    assert stats["win_rate"] == 0.75
    assert stats["total_pnl"] == -70.0
    assert stats["expectancy"] < 0        # the number that tells the truth


# --------------------------- session stats: execution ---------------------------

def test_crossing_costs_edge_and_resting_earns_it():
    """Same 102/103 market, mid 102.5, both sides."""
    stats = session_stats([
        execution_fill(1, "buy", 103, 10, 102, 103, 1),    # crossed: paid 0.5
        execution_fill(2, "sell", 103, 10, 102, 103, 0),   # rested:  earned 0.5
    ])

    assert stats["avg_edge_crossing"] == 0.5
    assert stats["avg_edge_passive"] == -0.5
    assert stats["crossed"] == 1
    assert stats["cross_rate"] == 0.5


def test_a_selling_aggressor_also_shows_a_positive_cost():
    """The sign convention must not depend on which side you traded."""
    stats = session_stats([
        execution_fill(1, "sell", 102, 10, 102, 103, 1),   # hit the bid: paid 0.5
    ])

    assert stats["avg_edge_crossing"] == 0.5


def test_total_spread_cost_is_weighted_by_size():
    stats = session_stats([
        execution_fill(1, "buy", 103, 100, 102, 103, 1),   # 0.5 x 100 = 50
        execution_fill(2, "sell", 103, 10, 102, 103, 0),   # -0.5 x 10 = -5
    ])

    assert stats["total_spread_cost"] == 45.0


def test_crossing_and_resting_can_cancel_out():
    stats = session_stats([
        execution_fill(1, "buy", 103, 10, 102, 103, 1),
        execution_fill(2, "sell", 103, 10, 102, 103, 0),
    ])

    assert stats["total_spread_cost"] == 0.0


def test_fills_without_a_mid_are_excluded_not_counted_as_zero():
    """A one-sided book gives no mid, so those fills cannot be measured."""
    stats = session_stats([
        execution_fill(1, "buy", 103, 10, None, 103, 1),   # unmeasurable
        execution_fill(2, "buy", 103, 10, 102, 103, 1),    # measurable, +0.5
    ])

    assert stats["fills"] == 2                 # both happened
    assert stats["cross_rate"] == 1.0          # but only one was measurable
    assert stats["avg_edge_crossing"] == 0.5   # not 0.25, which averaging in a zero would give
    assert stats["total_spread_cost"] == 5.0


def test_execution_cost_is_invisible_in_the_direction_stats():
    """The point of measuring both: you can be right and still lose to the spread."""
    stats = session_stats([
        execution_fill(1, "buy", 103, 10, 102, 103, 1),
        execution_fill(2, "sell", 104, 10, 104, 105, 1),
    ])

    assert stats["total_pnl"] == 10.0          # direction looks fine
    assert stats["total_spread_cost"] == 10.0  # and all of it went on spread


def test_crossings_are_counted_even_when_unmeasurable():
    """The aggressor flag is always known; a one-sided book only stops us
    measuring the edge, not knowing that we crossed."""
    stats = session_stats([
        execution_fill(1, "buy", 103, 10, None, 103, 1),   # crossed, no mid
        execution_fill(2, "buy", 103, 10, 102, 103, 1),    # crossed, measurable
    ])

    assert stats["fills"] == 2
    assert stats["measured"] == 1
    assert stats["crossed"] == 2                # both crossings counted
    assert stats["cross_rate"] == 1.0
    assert stats["avg_edge_crossing"] == 0.5    # averaged over the measurable one only


# --------------------------- excursions (MAE / MFE) ---------------------------

def marks_from(*pairs):
    return [{"timestamp": t, "price": p} for t, p in pairs]


PATH = marks_from((1, 100), (2, 99), (3, 96), (4, 101), (5, 112), (6, 104))


def trip_over(direction, entry, opened=1, closed=6):
    return {"opened_at": opened, "closed_at": closed,
            "direction": direction, "entry_price": entry}


def test_mae_and_mfe_on_a_long():
    """Dipped 4 against you before running 12 in your favour."""
    assert excursions(trip_over("long", 100.0), PATH) == {"mae": -4.0, "mfe": 12.0}


def test_the_extremes_swap_for_a_short():
    """A short suffers when price rises, so the same path inverts."""
    assert excursions(trip_over("short", 100.0), PATH) == {"mae": -12.0, "mfe": 4.0}


def test_only_marks_inside_the_trip_window_count():
    """A dip an hour after you closed says nothing about this trade."""
    early = excursions(trip_over("long", 100.0, opened=1, closed=3), PATH)

    assert early == {"mae": -4.0, "mfe": 0.0}     # never saw the run to 112


def test_marks_before_the_trip_are_ignored():
    late = excursions(trip_over("long", 101.0, opened=4, closed=6), PATH)

    assert late["mae"] == 0.0                     # the dip to 96 was before entry
    assert late["mfe"] == 11.0


def test_no_marks_in_the_window_is_not_measurable():
    """None rather than zero: zero would claim the price never moved."""
    assert excursions(trip_over("long", 100.0, opened=50, closed=60), PATH) == {
        "mae": None, "mfe": None,
    }


def test_excursion_is_measured_from_the_average_entry_not_the_first_mark():
    """Scaling in means the entry is a blend, and the first mark in the window
    may be somebody else's trade at an unrelated price."""
    trip = {"opened_at": 1, "closed_at": 6, "direction": "long", "entry_price": 105.0}

    result = excursions(trip, PATH)

    assert result["mae"] == -9.0                  # 96 against an entry of 105
    assert result["mfe"] == 7.0                   # 112 against an entry of 105


def test_a_trade_that_only_ever_won_has_no_adverse_excursion():
    rising = marks_from((1, 100), (2, 103), (3, 108))
    trip = {"opened_at": 1, "closed_at": 3, "direction": "long", "entry_price": 100.0}

    assert excursions(trip, rising) == {"mae": 0.0, "mfe": 8.0}


def test_mae_never_exceeds_mfe():
    for direction in ("long", "short"):
        result = excursions(trip_over(direction, 100.0), PATH)
        assert result["mae"] <= result["mfe"]


# --------------------------- session stats: excursions ---------------------------

def test_excursion_stats_need_marks():
    """Without a price path there is nothing to measure against."""
    stats = session_stats([fill(1, "buy", 100, 10), fill(2, "sell", 105, 10)])

    assert stats["avg_mae"] == 0.0
    assert stats["avg_mfe"] == 0.0
    assert stats["capture_rate"] == 0.0


def test_capture_rate_exposes_a_winner_cut_short():
    """The headline use: right about direction, took a quarter of the move."""
    fills = [fill(1, "buy", 100, 10), fill(10, "sell", 103, 10)]
    marks = marks_from((1, 100), (3, 96), (5, 112), (9, 105), (10, 103))

    stats = session_stats(fills, marks)

    assert stats["avg_mae"] == -4.0          # went 4 against you first
    assert stats["avg_mfe"] == 12.0          # was worth 12 at its best
    assert stats["capture_rate"] == 0.25     # you took 3 of the 12


def test_capture_rate_can_exceed_one():
    """Exiting better than any recorded mark is possible and must not be clamped."""
    fills = [fill(1, "buy", 100, 10), fill(5, "sell", 120, 10)]
    marks = marks_from((1, 100), (3, 105))

    assert session_stats(fills, marks)["capture_rate"] > 1.0


def test_capture_rate_is_negative_when_a_winner_became_a_loser():
    fills = [fill(1, "buy", 100, 10), fill(5, "sell", 97, 10)]
    marks = marks_from((1, 100), (2, 108), (5, 97))

    stats = session_stats(fills, marks)

    assert stats["avg_mfe"] == 8.0           # it was 8 up at one point
    assert stats["capture_rate"] < 0         # and you finished down


def test_a_trade_with_no_favourable_excursion_does_not_divide_by_zero():
    fills = [fill(1, "buy", 100, 10), fill(5, "sell", 94, 10)]
    marks = marks_from((1, 100), (3, 94), (5, 94))

    stats = session_stats(fills, marks)

    assert stats["avg_mfe"] == 0.0
    assert stats["capture_rate"] == 0.0


def test_trips_without_marks_in_their_window_are_excluded_not_zeroed():
    """Averaging in a zero would understate how far trades actually travel."""
    fills = [
        fill(1, "buy", 100, 10), fill(5, "sell", 104, 10),      # covered by marks
        fill(90, "buy", 100, 10), fill(95, "sell", 104, 10),    # no marks in window
    ]
    marks = marks_from((1, 100), (3, 112), (5, 104))

    stats = session_stats(fills, marks)

    assert stats["trips"] == 2
    assert stats["avg_mfe"] == 12.0          # not 6.0, which averaging in a zero gives


def test_capture_rate_weights_by_opportunity_not_by_trade_count():
    """Totals over totals, so a trivial trade cannot dominate the ratio."""
    fills = [
        fill(1, "buy", 100, 100), fill(5, "sell", 110, 100),    # big, captured 10 of 10
        fill(20, "buy", 100, 1), fill(25, "sell", 100, 1),      # tiny, captured 0 of 1
    ]
    marks = marks_from((1, 100), (3, 110), (5, 110), (20, 100), (22, 101), (25, 100))

    stats = session_stats(fills, marks)

    # 10 captured of 11 available, not the 50% an averaged-ratios approach gives
    assert stats["capture_rate"] == pytest.approx(10 / 11)
