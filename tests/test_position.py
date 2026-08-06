import pytest

from order_book.enums import Side
from order_book.position import Position


def build(*fills):
    """fills are (side, price, quantity) tuples applied in order."""
    p = Position()
    for side, price, quantity in fills:
        p.apply_fill(side, price, quantity)
    return p


def test_new_position_is_flat():
    p = Position()

    assert p.quantity == 0
    assert p.average_price == 0.0
    assert p.realised_pnl == 0.0


# --------------------------- increasing ---------------------------

def test_first_fill_sets_the_average():
    p = build((Side.BUY, 100, 10))

    assert p.quantity == 10
    assert p.average_price == 100.0
    assert p.realised_pnl == 0.0


def test_adding_to_a_long_blends_the_average():
    assert build((Side.BUY, 100, 10), (Side.BUY, 110, 10)).average_price == 105.0


def test_blend_is_weighted_by_size_not_a_midpoint():
    p = build((Side.BUY, 100, 10), (Side.BUY, 110, 30))

    assert p.quantity == 40
    assert p.average_price == 107.5


def test_adding_to_a_short_blends_the_average():
    p = build((Side.SELL, 100, 10), (Side.SELL, 90, 10))

    assert p.quantity == -20
    assert p.average_price == 95.0


def test_increasing_never_realises():
    assert build((Side.BUY, 100, 10), (Side.BUY, 110, 10)).realised_pnl == 0.0


# --------------------------- reducing ---------------------------

@pytest.mark.parametrize(
    "entry, exit_fill, expected_realised",
    [
        ((Side.BUY, 100, 10), (Side.SELL, 105, 4), 20.0),    # long, in profit
        ((Side.BUY, 100, 10), (Side.SELL, 95, 4), -20.0),    # long, at a loss
        ((Side.SELL, 100, 10), (Side.BUY, 90, 4), 40.0),     # short, in profit
        ((Side.SELL, 100, 10), (Side.BUY, 110, 4), -40.0),   # short, at a loss
    ],
)
def test_reducing_realises_with_the_right_sign(entry, exit_fill, expected_realised):
    assert build(entry, exit_fill).realised_pnl == expected_realised


def test_reducing_leaves_the_average_untouched():
    """The units still held were bought at the original price."""
    p = build((Side.BUY, 100, 10), (Side.SELL, 105, 8))

    assert p.quantity == 2
    assert p.average_price == 100.0


# --------------------------- closing out ---------------------------

def test_closing_a_long_flattens_and_clears_the_average():
    p = build((Side.BUY, 100, 10), (Side.SELL, 105, 10))

    assert p.quantity == 0
    assert p.average_price == 0.0
    assert p.realised_pnl == 50.0


def test_closing_a_short_flattens_and_clears_the_average():
    p = build((Side.SELL, 100, 10), (Side.BUY, 90, 10))

    assert p.quantity == 0
    assert p.average_price == 0.0
    assert p.realised_pnl == 100.0


# --------------------------- flipping ---------------------------

def test_flipping_long_to_short_rebases_at_the_fill_price():
    p = build((Side.BUY, 100, 10), (Side.SELL, 110, 15))

    assert p.quantity == -5
    assert p.realised_pnl == 100.0     # only the 10 that closed
    assert p.average_price == 110.0    # the new short opened here


def test_flipping_short_to_long_rebases_at_the_fill_price():
    p = build((Side.SELL, 100, 8), (Side.BUY, 95, 20))

    assert p.quantity == 12
    assert p.realised_pnl == 40.0
    assert p.average_price == 95.0


def test_a_flip_is_flat_against_its_own_fill_price():
    """Regression: the new position must not inherit the closed one's basis."""
    p = build((Side.BUY, 100, 10), (Side.SELL, 110, 15))

    assert p.unrealised_pnl(110) == 0.0


# --------------------------- marking to market ---------------------------

def test_unrealised_on_a_long():
    p = build((Side.BUY, 100, 10))

    assert p.unrealised_pnl(107) == 70.0
    assert p.unrealised_pnl(93) == -70.0


def test_unrealised_on_a_short_is_inverted():
    p = build((Side.SELL, 100, 10))

    assert p.unrealised_pnl(93) == 70.0
    assert p.unrealised_pnl(107) == -70.0


def test_flat_position_has_no_unrealised():
    p = build((Side.BUY, 100, 10), (Side.SELL, 105, 10))

    assert p.unrealised_pnl(999) == 0.0


def test_unrealised_is_zero_without_a_mark_price():
    p = build((Side.BUY, 100, 10))

    assert p.unrealised_pnl(None) == 0.0
    assert p.total_pnl(None) == 0.0


def test_total_pnl_combines_both_halves():
    p = build((Side.BUY, 100, 10), (Side.SELL, 105, 5))

    assert p.realised_pnl == 25.0
    assert p.unrealised_pnl(110) == 50.0
    assert p.total_pnl(110) == 75.0


# --------------------------- sequences ---------------------------

def test_round_trip_through_flat_accumulates_realised():
    p = build(
        (Side.BUY, 100, 10),
        (Side.SELL, 105, 10),
        (Side.SELL, 105, 10),
        (Side.BUY, 100, 10),
    )

    assert p.quantity == 0
    assert p.average_price == 0.0
    assert p.realised_pnl == 100.0


def test_total_pnl_matches_cash_flow_when_flat():
    """Starting and ending flat, profit is just sells minus buys."""
    fills = [
        (Side.BUY, 100, 10),
        (Side.BUY, 104, 10),
        (Side.SELL, 111, 5),
        (Side.SELL, 108, 15),
    ]
    paid = sum(pr * q for s, pr, q in fills if s == Side.BUY)
    received = sum(pr * q for s, pr, q in fills if s == Side.SELL)

    p = build(*fills)

    assert p.quantity == 0
    assert p.realised_pnl == received - paid
