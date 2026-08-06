from order_book.book import OrderBook
from order_book.enums import OrderType, Side
from order_book.order import Order


def make_order(order_id, side, price, quantity):
    return Order(
        order_id=order_id,
        side=side,
        price=price,
        order_type=OrderType.LIMIT,
        original_quantity=quantity,
    )


def make_market_order(order_id, side, quantity):
    return Order(
        order_id=order_id,
        side=side,
        price=None,
        order_type=OrderType.MARKET,
        original_quantity=quantity,
    )


def trade_fields(trade):
    return (
        trade.price,
        trade.quantity,
        trade.buyer_order_id,
        trade.seller_order_id,
    )


def test_empty_book_has_no_best_prices():
    book = OrderBook()

    assert book.best_bid() is None
    assert book.best_ask() is None


def test_non_crossing_order_rests_in_book():
    book = OrderBook()
    sell = make_order(1, Side.SELL, 100, 10)

    trades = book.add_order(sell)

    assert trades == []
    assert book.best_ask() == 100
    assert book.best_bid() is None
    assert book.order_id_to_order[1] is sell


def test_full_match_removes_resting_order_and_price_level():
    book = OrderBook()
    sell = make_order(1, Side.SELL, 100, 10)
    buy = make_order(2, Side.BUY, 100, 10)

    book.add_order(sell)
    trades = book.add_order(buy)

    assert [trade_fields(t) for t in trades] == [(100, 10, 2, 1)]
    assert sell.remaining_quantity == 0
    assert buy.remaining_quantity == 0
    assert book.best_ask() is None
    assert 1 not in book.order_id_to_order
    assert book.ask_prices_to_orders == {}


def test_partial_fill_leaves_resting_order_in_book():
    book = OrderBook()
    sell = make_order(1, Side.SELL, 100, 10)
    buy = make_order(2, Side.BUY, 100, 4)

    book.add_order(sell)
    trades = book.add_order(buy)

    assert [trade_fields(t) for t in trades] == [(100, 4, 2, 1)]
    assert sell.remaining_quantity == 6
    assert buy.remaining_quantity == 0
    assert book.best_ask() == 100
    assert 1 in book.order_id_to_order


def test_incoming_order_partially_filled_rests_for_remainder():
    book = OrderBook()
    sell = make_order(1, Side.SELL, 100, 4)
    buy = make_order(2, Side.BUY, 100, 10)

    book.add_order(sell)
    trades = book.add_order(buy)

    assert [trade_fields(t) for t in trades] == [(100, 4, 2, 1)]
    assert buy.remaining_quantity == 6
    assert book.best_ask() is None
    assert book.best_bid() == 100
    assert book.order_id_to_order[2] is buy


def test_price_time_priority_within_same_price_level():
    book = OrderBook()
    sell1 = make_order(1, Side.SELL, 100, 5)
    sell2 = make_order(2, Side.SELL, 100, 5)
    buy = make_order(3, Side.BUY, 100, 5)

    book.add_order(sell1)
    book.add_order(sell2)
    trades = book.add_order(buy)

    assert [trade_fields(t) for t in trades] == [(100, 5, 3, 1)]
    assert sell1.remaining_quantity == 0
    assert sell2.remaining_quantity == 5
    assert 1 not in book.order_id_to_order
    assert 2 in book.order_id_to_order


def test_incoming_buy_matches_best_ask_first_across_price_levels():
    book = OrderBook()
    sell_high = make_order(1, Side.SELL, 105, 5)
    sell_low = make_order(2, Side.SELL, 100, 5)
    buy = make_order(3, Side.BUY, 105, 10)

    book.add_order(sell_high)
    book.add_order(sell_low)
    trades = book.add_order(buy)

    assert [trade_fields(t) for t in trades] == [(100, 5, 3, 2), (105, 5, 3, 1)]
    assert book.best_ask() is None


def test_incoming_sell_matches_best_bid_first_across_price_levels():
    book = OrderBook()
    buy_low = make_order(1, Side.BUY, 95, 5)
    buy_high = make_order(2, Side.BUY, 100, 5)
    sell = make_order(3, Side.SELL, 95, 10)

    book.add_order(buy_low)
    book.add_order(buy_high)
    trades = book.add_order(sell)

    assert [trade_fields(t) for t in trades] == [(100, 5, 2, 3), (95, 5, 1, 3)]
    assert book.best_bid() is None


def test_non_crossing_price_does_not_match():
    book = OrderBook()
    sell = make_order(1, Side.SELL, 105, 10)
    buy = make_order(2, Side.BUY, 100, 10)

    book.add_order(sell)
    trades = book.add_order(buy)

    assert trades == []
    assert book.best_ask() == 105
    assert book.best_bid() == 100


def test_cancel_nonexistent_order_returns_false():
    book = OrderBook()

    assert book.cancel_order(999) is False


def test_cancel_resting_order_removes_it_and_price_level():
    book = OrderBook()
    sell = make_order(1, Side.SELL, 100, 10)
    book.add_order(sell)

    result = book.cancel_order(1)

    assert result is True
    assert book.best_ask() is None
    assert 1 not in book.order_id_to_order
    assert book.ask_prices_to_orders == {}


def test_cancel_one_of_two_orders_at_same_price_keeps_the_other():
    book = OrderBook()
    buy1 = make_order(1, Side.BUY, 100, 5)
    buy2 = make_order(2, Side.BUY, 100, 5)
    book.add_order(buy1)
    book.add_order(buy2)

    result = book.cancel_order(1)

    assert result is True
    assert 1 not in book.order_id_to_order
    assert 2 in book.order_id_to_order
    assert book.best_bid() == 100
    assert list(book.bid_prices_to_orders[100]) == [buy2]


def test_spread_is_none_when_either_side_missing():
    book = OrderBook()

    assert book.spread() is None

    book.add_order(make_order(1, Side.SELL, 105, 10))
    assert book.spread() is None


def test_spread_is_ask_minus_bid():
    book = OrderBook()
    book.add_order(make_order(1, Side.SELL, 105, 10))
    book.add_order(make_order(2, Side.BUY, 100, 10))

    assert book.spread() == 5


def test_depth_sums_quantity_per_price_level():
    book = OrderBook()
    book.add_order(make_order(1, Side.BUY, 100, 5))
    book.add_order(make_order(2, Side.BUY, 100, 3))
    book.add_order(make_order(3, Side.BUY, 99, 2))
    book.add_order(make_order(4, Side.SELL, 105, 7))

    assert book.bid_depth() == {100: 8, 99: 2}
    assert book.ask_depth() == {105: 7}


def test_depth_empty_when_no_orders():
    book = OrderBook()

    assert book.bid_depth() == {}
    assert book.ask_depth() == {}


def test_trade_log_accumulates_across_multiple_add_order_calls():
    book = OrderBook()
    book.add_order(make_order(1, Side.SELL, 100, 5))
    book.add_order(make_order(2, Side.SELL, 100, 5))

    trades_a = book.add_order(make_order(3, Side.BUY, 100, 5))
    trades_b = book.add_order(make_order(4, Side.BUY, 100, 5))

    assert book.trade_log == trades_a + trades_b
    assert len(book.trade_log) == 2


def test_market_buy_sweeps_ask_levels_at_resting_prices():
    book = OrderBook()
    book.add_order(make_order(1, Side.SELL, 100, 5))
    book.add_order(make_order(2, Side.SELL, 101, 5))
    book.add_order(make_order(3, Side.SELL, 102, 5))

    trades = book.add_order(make_market_order(4, Side.BUY, 12))

    assert [trade_fields(t) for t in trades] == [
        (100, 5, 4, 1),
        (101, 5, 4, 2),
        (102, 2, 4, 3),
    ]
    assert book.best_ask() == 102
    assert book.ask_depth() == {102: 3}


def test_market_sell_sweeps_bid_levels_best_first():
    book = OrderBook()
    book.add_order(make_order(1, Side.BUY, 95, 5))
    book.add_order(make_order(2, Side.BUY, 100, 5))

    trades = book.add_order(make_market_order(3, Side.SELL, 8))

    assert [trade_fields(t) for t in trades] == [(100, 5, 2, 3), (95, 3, 1, 3)]
    assert book.best_bid() == 95
    assert book.bid_depth() == {95: 2}


def test_unfilled_market_order_remainder_is_discarded():
    book = OrderBook()
    book.add_order(make_order(1, Side.SELL, 100, 40))
    market = make_market_order(2, Side.BUY, 100)

    trades = book.add_order(market)

    assert [trade_fields(t) for t in trades] == [(100, 40, 2, 1)]
    assert market.remaining_quantity == 60
    # the remainder must not rest, must not be indexed, and must not pollute the heap
    assert book.best_bid() is None
    assert book.bid_depth() == {}
    assert book.bid_prices_heap == []
    assert 2 not in book.order_id_to_order


def test_market_order_against_empty_book_does_nothing():
    book = OrderBook()
    market = make_market_order(1, Side.BUY, 10)

    trades = book.add_order(market)

    assert trades == []
    assert market.remaining_quantity == 10
    assert book.best_bid() is None
    assert book.order_id_to_order == {}


def test_market_order_ignores_price_and_takes_whatever_is_there():
    """A limit buy at 100 would not cross a 500 ask; a market buy must."""
    book = OrderBook()
    book.add_order(make_order(1, Side.SELL, 500, 3))

    trades = book.add_order(make_market_order(2, Side.BUY, 3))

    assert [trade_fields(t) for t in trades] == [(500, 3, 2, 1)]
    assert book.best_ask() is None


def test_cancelled_order_does_not_participate_in_later_matches():
    book = OrderBook()
    sell = make_order(1, Side.SELL, 100, 10)
    book.add_order(sell)
    book.cancel_order(1)

    buy = make_order(2, Side.BUY, 100, 10)
    trades = book.add_order(buy)

    assert trades == []
    assert buy.remaining_quantity == 10
    assert book.best_bid() == 100
