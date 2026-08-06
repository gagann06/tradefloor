import pytest

from order_book.api import create_app


@pytest.fixture
def app():
    # in-memory journal so tests neither touch nor create journal.db
    application = create_app(journal_path=":memory:")
    application.testing = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def test_health_check(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_submit_order_rests_when_no_cross(client):
    resp = client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["order_id"] == 1
    assert body["remaining_quantity"] == 10
    assert body["trades"] == []


def test_submit_crossing_orders_produces_trade(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})
    resp = client.post("/orders", json={"side": "buy", "price": 100, "quantity": 4})

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["remaining_quantity"] == 0
    assert body["trades"] == [
        {
            "price": 100,
            "quantity": 4,
            "buyer_order_id": 2,
            "seller_order_id": 1,
            "timestamp": body["trades"][0]["timestamp"],
        }
    ]


def test_submit_order_missing_body_returns_400(client):
    resp = client.post("/orders")

    assert resp.status_code == 400


def test_submit_order_invalid_side_returns_400(client):
    resp = client.post("/orders", json={"side": "hold", "price": 100, "quantity": 10})

    assert resp.status_code == 400


def test_submit_order_invalid_quantity_returns_400(client):
    resp = client.post("/orders", json={"side": "buy", "price": 100, "quantity": -5})

    assert resp.status_code == 400


def test_submit_market_order_with_a_price_returns_400(client):
    resp = client.post(
        "/orders",
        json={"side": "buy", "price": 100, "quantity": 10, "order_type": "market"},
    )

    assert resp.status_code == 400


def test_submit_unknown_order_type_returns_400(client):
    resp = client.post(
        "/orders",
        json={"side": "buy", "price": 100, "quantity": 10, "order_type": "iceberg"},
    )

    assert resp.status_code == 400


def test_market_order_fills_against_resting_liquidity(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 6})
    client.post("/orders", json={"side": "sell", "price": 101, "quantity": 6})

    resp = client.post(
        "/orders", json={"side": "buy", "quantity": 10, "order_type": "market"}
    )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["filled_quantity"] == 10
    assert body["remaining_quantity"] == 0
    assert body["resting"] is False
    assert [(t["price"], t["quantity"]) for t in body["trades"]] == [(100, 6), (101, 4)]


def test_market_order_remainder_is_cancelled_not_rested(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 4})

    resp = client.post(
        "/orders", json={"side": "buy", "quantity": 10, "order_type": "market"}
    )

    body = resp.get_json()
    assert body["filled_quantity"] == 4
    assert body["remaining_quantity"] == 6
    assert body["resting"] is False

    # the unfilled 6 must not show up as a bid, and must not be cancellable
    snapshot = client.get("/book/snapshot").get_json()
    assert snapshot["bids"] == {}
    assert snapshot["best_bid"] is None
    assert client.delete(f"/orders/{body['order_id']}").status_code == 404


def test_market_order_into_empty_book_fills_nothing(client):
    resp = client.post(
        "/orders", json={"side": "sell", "quantity": 10, "order_type": "market"}
    )

    body = resp.get_json()
    assert resp.status_code == 201
    assert body["filled_quantity"] == 0
    assert body["trades"] == []
    assert body["resting"] is False


def test_resting_limit_order_reports_resting_true(client):
    resp = client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10})

    body = resp.get_json()
    assert body["resting"] is True
    assert body["filled_quantity"] == 0
    assert body["remaining_quantity"] == 10


def test_limit_order_without_price_returns_400(client):
    resp = client.post("/orders", json={"side": "buy", "quantity": 10})

    assert resp.status_code == 400


def test_cancel_existing_order(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})
    resp = client.delete("/orders/1")

    assert resp.status_code == 200
    assert resp.get_json() == {"order_id": 1, "cancelled": True}


def test_cancel_nonexistent_order_returns_404(client):
    resp = client.delete("/orders/999")

    assert resp.status_code == 404


def test_book_best_reflects_resting_orders(client):
    client.post("/orders", json={"side": "sell", "price": 105, "quantity": 10})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10})

    resp = client.get("/book/best")

    assert resp.get_json() == {"best_bid": 100, "best_ask": 105, "spread": 5}


def test_book_depth_reflects_resting_quantity(client):
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 5})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 3})
    client.post("/orders", json={"side": "sell", "price": 105, "quantity": 7})

    resp = client.get("/book/depth")

    assert resp.get_json() == {"bids": {"100": 8}, "asks": {"105": 7}}


def test_trades_endpoint_accumulates_history(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 4})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 6})

    resp = client.get("/trades")

    body = resp.get_json()
    assert len(body) == 2
    assert [t["quantity"] for t in body] == [4, 6]


def test_index_serves_the_simulator_page(client):
    resp = client.get("/")

    assert resp.status_code == 200
    # anchor on the ladder mount point rather than branding, which is cosmetic
    assert b'id="ladder"' in resp.data


def test_snapshot_on_empty_book(client):
    body = client.get("/book/snapshot").get_json()

    assert body == {
        "best_bid": None,
        "best_ask": None,
        "spread": None,
        "bids": {},
        "asks": {},
        "last_price": None,
        "prev_price": None,
        "session_high": None,
        "session_low": None,
        "trade_count": 0,
        "total_volume": 0,
        "volume_at_price": {},
        "working_orders": [],
        "recent_trades": [],
        "position": {
            "quantity": 0,
            "average_price": 0.0,
            "realised_pnl": 0.0,
            "unrealised_pnl": 0.0,
            "total_pnl": 0.0,
            "mark_price": None,
        },
    }


def test_snapshot_reports_book_and_trade_state(client):
    client.post("/orders", json={"side": "sell", "price": 105, "quantity": 10})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 8})
    client.post("/orders", json={"side": "buy", "price": 105, "quantity": 4})

    body = client.get("/book/snapshot").get_json()

    assert body["best_bid"] == 100
    assert body["best_ask"] == 105
    assert body["spread"] == 5
    assert body["bids"] == {"100": 8}
    assert body["asks"] == {"105": 6}
    assert body["last_price"] == 105
    assert body["trade_count"] == 1
    assert body["total_volume"] == 4
    assert len(body["recent_trades"]) == 1


def test_snapshot_trade_limit_returns_most_recent(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 3})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 4})

    body = client.get("/book/snapshot?trades=1").get_json()

    assert body["trade_count"] == 2
    assert [t["quantity"] for t in body["recent_trades"]] == [4]


def test_trades_limit_returns_most_recent(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 3})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 4})

    body = client.get("/trades?limit=1").get_json()

    assert [t["quantity"] for t in body] == [4]


def test_seed_builds_a_two_sided_book_in_one_call(client):
    resp = client.post("/book/seed", json={"mid": 100, "levels": 3, "min_size": 5, "max_size": 5})

    assert resp.status_code == 201
    assert resp.get_json()["count"] == 6

    snapshot = client.get("/book/snapshot").get_json()
    assert snapshot["bids"] == {"97": 5, "98": 5, "99": 5}
    assert snapshot["asks"] == {"101": 5, "102": 5, "103": 5}
    assert snapshot["best_bid"] == 99
    assert snapshot["best_ask"] == 101
    # seeded levels straddle the mid, so nothing should have crossed
    assert snapshot["trade_count"] == 0


def test_seed_uses_defaults_with_no_body(client):
    assert client.post("/book/seed").status_code == 201

    snapshot = client.get("/book/snapshot").get_json()
    assert len(snapshot["bids"]) == 7
    assert len(snapshot["asks"]) == 7
    assert snapshot["trade_count"] == 0


def test_seeded_orders_are_cancellable_like_any_other(client):
    created = client.post("/book/seed", json={"levels": 2}).get_json()["created"]

    assert client.delete(f"/orders/{created[0]}").status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"levels": 0},
        {"levels": 51},
        {"mid": 3, "levels": 7},
        {"min_size": 0},
        {"min_size": 10, "max_size": 4},
        {"levels": "many"},
    ],
)
def test_seed_rejects_invalid_parameters(client, body):
    assert client.post("/book/seed", json=body).status_code == 400


def test_reset_clears_book_and_order_ids(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})

    assert client.post("/book/reset").status_code == 200

    snapshot = client.get("/book/snapshot").get_json()
    assert snapshot["best_ask"] is None
    assert snapshot["trade_count"] == 0

    resp = client.post("/orders", json={"side": "buy", "price": 100, "quantity": 5})
    assert resp.get_json()["order_id"] == 1


def test_get_working_order_returns_its_state(client):
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10})

    body = client.get("/orders/1").get_json()

    assert body["order_id"] == 1
    assert body["side"] == "buy"
    assert body["price"] == 100
    assert body["order_type"] == "limit"
    assert body["original_quantity"] == 10
    assert body["remaining_quantity"] == 10


def test_get_order_reflects_partial_fill(client):
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10})
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 4})

    body = client.get("/orders/1").get_json()

    assert body["original_quantity"] == 10
    assert body["remaining_quantity"] == 6


def test_get_filled_order_returns_404(client):
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10})
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})

    assert client.get("/orders/1").status_code == 404


def test_get_unknown_order_returns_404(client):
    assert client.get("/orders/999").status_code == 404


def test_bulk_order_lookup_omits_unknown_ids(client):
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10})
    client.post("/orders", json={"side": "buy", "price": 99, "quantity": 5})

    body = client.get("/orders?ids=1,2,999").get_json()

    assert [o["order_id"] for o in body] == [1, 2]


def test_bulk_order_lookup_with_no_ids_returns_empty(client):
    assert client.get("/orders").get_json() == []
    assert client.get("/orders?ids=").get_json() == []


def test_bulk_order_lookup_rejects_non_integer_ids(client):
    assert client.get("/orders?ids=1,abc").status_code == 400


def test_snapshot_reports_volume_profile_and_session_range(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 5})
    client.post("/orders", json={"side": "sell", "price": 102, "quantity": 5})
    # sweeps the cheaper 100 level first, then takes 3 of the 102 level
    client.post("/orders", json={"side": "buy", "price": 102, "quantity": 8})
    # does not cross the 2 left at 102, so it just rests as a bid
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 2})

    body = client.get("/book/snapshot").get_json()

    assert body["volume_at_price"] == {"100": 5, "102": 3}
    assert body["session_low"] == 100
    assert body["session_high"] == 102
    assert body["total_volume"] == 8
    assert body["last_price"] == 102
    assert body["prev_price"] == 100


def test_snapshot_returns_requested_working_orders(client):
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10})
    client.post("/orders", json={"side": "buy", "price": 99, "quantity": 5})
    client.post("/orders", json={"side": "sell", "price": 110, "quantity": 7})

    body = client.get("/book/snapshot?working=1,3,999").get_json()

    assert [o["order_id"] for o in body["working_orders"]] == [1, 3]
    assert body["working_orders"][0]["price"] == 100
    assert body["working_orders"][1]["side"] == "sell"


def test_snapshot_working_orders_drop_out_once_filled(client):
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10})
    assert client.get("/book/snapshot?working=1").get_json()["working_orders"] != []

    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10})

    assert client.get("/book/snapshot?working=1").get_json()["working_orders"] == []


def test_snapshot_working_orders_reject_non_integer_ids(client):
    assert client.get("/book/snapshot?working=1,nope").status_code == 400


def test_snapshot_session_range_is_none_before_any_trade(client):
    body = client.get("/book/snapshot").get_json()

    assert body["session_high"] is None
    assert body["session_low"] is None
    assert body["prev_price"] is None
    assert body["volume_at_price"] == {}


def test_position_starts_flat(client):
    body = client.get("/positions/me").get_json()

    assert body["quantity"] == 0
    assert body["average_price"] == 0.0
    assert body["realised_pnl"] == 0.0
    assert body["unrealised_pnl"] == 0.0


def test_aggressive_fill_moves_the_position(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10, "owner": "flow"})
    body = client.post(
        "/orders", json={"side": "buy", "quantity": 4, "order_type": "market", "owner": "me"}
    ).get_json()

    assert body["position"]["quantity"] == 4
    assert body["position"]["average_price"] == 100.0


def test_resting_order_filled_by_someone_else_still_credits_the_owner(client):
    """The trade is returned from the aggressor's call, not the resting owner's."""
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10, "owner": "me"})
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 6, "owner": "flow"})

    mine = client.get("/positions/me").get_json()
    theirs = client.get("/positions/flow").get_json()

    assert mine["quantity"] == 6          # my passive bid got lifted
    assert mine["average_price"] == 100.0
    assert theirs["quantity"] == -6       # and they are short the other side


def test_both_sides_of_a_fill_are_attributed_separately(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10, "owner": "me"})

    positions = client.get("/positions").get_json()

    assert positions["me"]["quantity"] == 10
    assert positions["flow"]["quantity"] == -10
    # a closed system: every long is someone else's short
    assert positions["me"]["quantity"] + positions["flow"]["quantity"] == 0


def test_realised_pnl_accrues_on_a_round_trip(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "quantity": 10, "order_type": "market", "owner": "me"})
    client.post("/orders", json={"side": "buy", "price": 105, "quantity": 10, "owner": "flow"})
    client.post("/orders", json={"side": "sell", "quantity": 10, "order_type": "market", "owner": "me"})

    me = client.get("/positions/me").get_json()

    assert me["quantity"] == 0
    assert me["realised_pnl"] == 50.0     # bought at 100, sold at 105
    assert me["average_price"] == 0.0


def test_snapshot_marks_position_at_the_last_traded_price(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 10, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "quantity": 10, "order_type": "market", "owner": "me"})
    # a later trade between other parties moves the mark
    client.post("/orders", json={"side": "sell", "price": 108, "quantity": 1, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "price": 108, "quantity": 1, "owner": "other"})

    body = client.get("/book/snapshot?owner=me").get_json()

    assert body["last_price"] == 108
    assert body["position"]["quantity"] == 10
    assert body["position"]["unrealised_pnl"] == 80.0   # 10 * (108 - 100)
    assert body["position"]["mark_price"] == 108


def test_snapshot_position_defaults_to_owner_me(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 5, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "quantity": 5, "order_type": "market"})

    assert client.get("/book/snapshot").get_json()["position"]["quantity"] == 5


def test_seeded_orders_are_owned_by_flow_not_me(client):
    client.post("/book/seed", json={"levels": 3})
    client.post("/orders", json={"side": "buy", "quantity": 5, "order_type": "market", "owner": "me"})

    assert client.get("/positions/me").get_json()["quantity"] == 5
    assert client.get("/positions/flow").get_json()["quantity"] == -5


def test_reset_clears_positions(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 5, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "quantity": 5, "order_type": "market", "owner": "me"})
    assert client.get("/positions/me").get_json()["quantity"] == 5

    client.post("/book/reset")

    assert client.get("/positions/me").get_json()["quantity"] == 0
    # reading a position must not resurrect it
    assert client.get("/positions").get_json() == {}


def fills_of(app):
    return app.journal.db.execute(
        """SELECT side, price, quantity, order_id, best_bid, best_ask, aggressor
           FROM fills ORDER BY id"""
    ).fetchall()


def test_a_session_is_open_from_startup(client):
    body = client.get("/journal/session").get_json()

    assert body["session_id"] is not None
    assert body["ended_at"] is None
    assert body["fills"] == 0


def test_only_the_humans_fills_are_journalled(app, client):
    """The synthetic flow trades constantly; journalling it would drown the data."""
    # two bots trading with each other: nothing of mine to record
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10, "owner": "flow"})
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 4, "owner": "other"})

    assert fills_of(app) == []

    # now I sell into what is left of the bot's bid
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 3, "owner": "me"})

    assert len(fills_of(app)) == 1


def test_crossing_is_recorded_as_aggressive(app, client):
    client.post("/orders", json={"side": "sell", "price": 103, "quantity": 10, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "price": 103, "quantity": 4, "owner": "me"})

    side, price, qty, _, _, _, aggressor = fills_of(app)[0]

    assert (side, price, qty) == ("buy", 103, 4)
    assert aggressor == 1


def test_being_hit_on_a_resting_order_is_recorded_as_passive(app, client):
    """The fill arrives via someone else's request, and must still be mine."""
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10, "owner": "me"})
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 6, "owner": "flow"})

    side, price, qty, _, _, _, aggressor = fills_of(app)[0]

    assert (side, price, qty) == ("buy", 100, 6)
    assert aggressor == 0


def test_touch_is_the_market_before_the_order_not_after(app, client):
    """The trap: add_order consumes the levels it fills, so reading the book
    afterwards reports where the market ended up, not where it started."""
    client.post("/orders", json={"side": "buy", "price": 99, "quantity": 5, "owner": "flow"})
    client.post("/orders", json={"side": "sell", "price": 101, "quantity": 10, "owner": "flow"})
    client.post("/orders", json={"side": "sell", "price": 102, "quantity": 10, "owner": "flow"})

    # sweeps 101 entirely and part of 102, leaving the best ask at 102
    client.post("/orders", json={"side": "buy", "quantity": 15, "order_type": "market", "owner": "me"})

    assert client.get("/book/best").get_json()["best_ask"] == 102

    for _, _, _, _, best_bid, best_ask, _ in fills_of(app):
        assert best_ask == 101      # arrival price, not the post-sweep 102
        assert best_bid == 99


def test_every_fill_of_a_sweep_is_journalled(app, client):
    client.post("/orders", json={"side": "sell", "price": 101, "quantity": 5, "owner": "flow"})
    client.post("/orders", json={"side": "sell", "price": 102, "quantity": 5, "owner": "flow"})

    client.post("/orders", json={"side": "buy", "quantity": 8, "order_type": "market", "owner": "me"})

    assert [(f[1], f[2]) for f in fills_of(app)] == [(101, 5), (102, 3)]


def test_a_one_sided_book_journals_a_null_touch(app, client):
    """Nothing bidding behind you is a real state, not a reason to lose the fill."""
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 5, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "quantity": 5, "order_type": "market", "owner": "me"})

    _, _, _, _, best_bid, best_ask, _ = fills_of(app)[0]

    assert best_bid is None
    assert best_ask == 100


def test_unfilled_orders_are_not_journalled(app, client):
    """A journal records fills, not intentions."""
    client.post("/orders", json={"side": "buy", "price": 50, "quantity": 10, "owner": "me"})

    assert fills_of(app) == []


def test_reset_closes_the_session_and_opens_another(app, client):
    first = client.get("/journal/session").get_json()["session_id"]
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 5, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "quantity": 5, "order_type": "market", "owner": "me"})

    body = client.post("/book/reset").get_json()

    assert body["ended_session"] == first
    assert body["session_id"] != first

    ended_at = app.journal.db.execute(
        "SELECT ended_at FROM sessions WHERE id = ?", (first,)
    ).fetchone()[0]
    assert ended_at is not None

    # the new session starts empty, but the old fills are still on record
    assert client.get("/journal/session").get_json()["fills"] == 0
    assert len(fills_of(app)) == 1


def test_journal_fills_endpoint_returns_this_session(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 5, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "quantity": 5, "order_type": "market", "owner": "me"})

    body = client.get("/journal/fills").get_json()

    assert len(body) == 1
    assert body[0]["side"] == "buy"
    assert body[0]["aggressor"] == 1

    client.post("/book/reset")
    assert client.get("/journal/fills").get_json() == []
    assert len(client.get("/journal/fills?all=1").get_json()) == 1


def test_journal_stats_on_an_untraded_session(client):
    body = client.get("/journal/stats").get_json()

    assert body["scope"] == "session"
    assert body["trips"] == 0
    assert body["fills"] == 0
    assert body["win_rate"] == 0.0


def test_journal_stats_scores_a_round_trip(client):
    client.post("/orders", json={"side": "sell", "price": 103, "quantity": 10, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "price": 100, "quantity": 10, "owner": "flow"})
    # cross in at 103, then hit the 100 bid to get out -> a losing trip
    client.post("/orders", json={"side": "buy", "quantity": 10, "order_type": "market", "owner": "me"})
    client.post("/orders", json={"side": "sell", "quantity": 10, "order_type": "market", "owner": "me"})

    body = client.get("/journal/stats").get_json()

    assert body["trips"] == 1
    assert body["losses"] == 1
    assert body["total_pnl"] == -30.0
    assert body["fills"] == 2
    assert body["crossed"] == 2              # both legs were aggressive
    assert body["cross_rate"] == 1.0
    assert body["measured"] == 1             # the exit emptied the book's far side
    assert body["total_spread_cost"] > 0


def test_journal_stats_all_spans_sessions(client):
    client.post("/orders", json={"side": "sell", "price": 100, "quantity": 5, "owner": "flow"})
    client.post("/orders", json={"side": "buy", "quantity": 5, "order_type": "market", "owner": "me"})
    client.post("/book/reset")

    assert client.get("/journal/stats").get_json()["fills"] == 0
    assert client.get("/journal/stats?all=1").get_json()["fills"] == 1
    assert client.get("/journal/stats?all=1").get_json()["scope"] == "all"


def test_fresh_app_per_test_has_isolated_state(client):
    resp = client.get("/book/best")

    assert resp.get_json() == {"best_bid": None, "best_ask": None, "spread": None}
