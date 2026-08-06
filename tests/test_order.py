import pytest

from order_book.enums import OrderType, Side
from order_book.order import Order


def make_order(**overrides):
    fields = dict(
        order_id=1,
        side=Side.BUY,
        price=100,
        order_type=OrderType.LIMIT,
        original_quantity=10,
    )
    fields.update(overrides)
    return Order(**fields)


def test_valid_order_sets_all_fields():
    order = make_order()

    assert order.order_id == 1
    assert order.side == Side.BUY
    assert order.price == 100
    assert order.order_type == OrderType.LIMIT
    assert order.original_quantity == 10
    assert isinstance(order.timestamp, int)


def test_remaining_quantity_starts_equal_to_original():
    order = make_order(original_quantity=10)

    assert order.remaining_quantity == order.original_quantity


@pytest.mark.parametrize("bad_quantity", [0, -5])
def test_non_positive_quantity_raises(bad_quantity):
    with pytest.raises(ValueError):
        make_order(original_quantity=bad_quantity)


@pytest.mark.parametrize("bad_price", [0, -1])
def test_non_positive_price_raises(bad_price):
    with pytest.raises(ValueError):
        make_order(price=bad_price)


def test_invalid_side_raises():
    with pytest.raises(ValueError):
        make_order(side="buy")


def test_invalid_order_type_raises():
    with pytest.raises(ValueError):
        make_order(order_type="limit")


def test_market_order_is_constructed_without_a_price():
    order = make_order(order_type=OrderType.MARKET, price=None)

    assert order.order_type == OrderType.MARKET
    assert order.price is None


def test_market_order_with_a_price_raises():
    with pytest.raises(ValueError):
        make_order(order_type=OrderType.MARKET, price=100)


def test_limit_order_without_a_price_raises():
    with pytest.raises(ValueError):
        make_order(order_type=OrderType.LIMIT, price=None)
