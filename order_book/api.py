import os
import random
import threading
from itertools import count

from flask import Flask, jsonify, render_template, request

from order_book.analysis import session_stats
from order_book.book import OrderBook
from order_book.enums import OrderType, Side
from order_book.journal import Journal
from order_book.order import Order
from order_book.position import Position

SIDE_MAP = {"buy": Side.BUY, "sell": Side.SELL}
ORDER_TYPE_MAP = {"limit": OrderType.LIMIT, "market": OrderType.MARKET}
JOURNAL_OWNER = "me"


def position_to_dict(position, mark_price):
    """JSON shape for a Position. Serialisation is an API concern, so it lives
    here rather than on the domain object."""
    return {
        "quantity": position.quantity,
        "average_price": round(position.average_price, 4),
        "realised_pnl": round(position.realised_pnl, 4),
        "unrealised_pnl": round(position.unrealised_pnl(mark_price), 4),
        "total_pnl": round(position.total_pnl(mark_price), 4),
        "mark_price": mark_price,
    }


def order_to_dict(order):
    return {
        "order_id": order.order_id,
        "side": order.side.value,
        "price": order.price,
        "order_type": order.order_type.value,
        "original_quantity": order.original_quantity,
        "remaining_quantity": order.remaining_quantity,
        "timestamp": order.timestamp,
    }


def trade_to_dict(trade):
    return {
        "price": trade.price,
        "quantity": trade.quantity,
        "buyer_order_id": trade.buyer_order_id,
        "seller_order_id": trade.seller_order_id,
        "timestamp": trade.timestamp,
    }


def create_app(journal_path=None):
    app = Flask(__name__)
    app.book = OrderBook()
    app.order_id_counter = count(1)
    # Only the human's fills are journalled. The synthetic flow produces
    # thousands of fills a minute between bots; recording those would swamp the
    # database and say nothing about how the person is trading.
    app.journal_owner = JOURNAL_OWNER
    app.journal = Journal(journal_path or os.environ.get("TRADEFLOOR_JOURNAL", "journal.db"))
    app.session_id = app.journal.start_session()
    # The matching engine is deliberately single-threaded and lock-free (real
    # engines are, for determinism). Concurrency is the API layer's problem, so
    # every mutation of the shared book is serialised here.
    app.book_lock = threading.Lock()
    # The engine matches orders and has no notion of who owns them, so ownership
    # and P&L live here. Every fill has two sides, and the trade is only ever
    # returned from the aggressor's add_order call — so both sides must be
    # attributed from that one result, not just the incoming order's.
    app.owners = {}        # order_id -> owner
    app.positions = {}     # owner -> Position

    def position_for(owner):
        """Get-or-create. Write paths only — this materialises the owner."""
        return app.positions.setdefault(owner, Position())

    def read_position(owner):
        """Read-only view. Never materialises, so a GET has no side effects."""
        return app.positions.get(owner) or Position()

    def attribute(trades):
        """Credit both sides of every fill. Caller must hold the lock."""
        for t in trades:
            buyer = app.owners.get(t.buyer_order_id)
            seller = app.owners.get(t.seller_order_id)
            if buyer is not None:
                position_for(buyer).apply_fill(Side.BUY, t.price, t.quantity)
            if seller is not None:
                position_for(seller).apply_fill(Side.SELL, t.price, t.quantity)

    def mark_price():
        """Last traded price, or None before anything has traded."""
        log = app.book.trade_log
        return log[-1].price if log else None

    def journal_rows(trades, incoming_order_id, arrival_bid, arrival_ask):
        """Build journal rows for the human's fills. Caller must hold the lock.

        The touch passed in is the *arrival* price — the market as it stood
        before add_order ran. It cannot be read afterwards: matching has already
        consumed the levels it filled against, so the book now shows where the
        market ended up rather than where it was when the order was sent. A
        multi-level sweep shares one arrival touch by design; that is the
        standard benchmark for measuring what the whole order cost.
        """
        rows = []
        for t in trades:
            for side, participant_id in (
                ("buy", t.buyer_order_id),
                ("sell", t.seller_order_id),
            ):
                if app.owners.get(participant_id) != app.journal_owner:
                    continue
                # add_order only returns fills caused by the incoming order, so
                # that order is the aggressor in every one of them; the other
                # side was resting and got hit.
                rows.append((
                    app.session_id, t.timestamp, side, t.price, t.quantity,
                    participant_id, arrival_bid, arrival_ask,
                    1 if participant_id == incoming_order_id else 0,
                ))
        return rows

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/orders")
    def submit_order():
        data = request.get_json(silent=True)
        if data is None:
            return jsonify(error="request body must be JSON"), 400

        side = SIDE_MAP.get(data.get("side"))
        order_type = ORDER_TYPE_MAP.get(data.get("order_type", "limit"))
        price = data.get("price")
        quantity = data.get("quantity")
        owner = str(data.get("owner", "me"))

        if side is None:
            return jsonify(error="side must be 'buy' or 'sell'"), 400
        if order_type is None:
            return jsonify(error="order_type must be 'limit' or 'market'"), 400

        with app.book_lock:
            order_id = next(app.order_id_counter)

        try:
            order = Order(
                order_id=order_id,
                side=side,
                price=price,
                order_type=order_type,
                original_quantity=quantity,
            )
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

        with app.book_lock:
            # capture the touch BEFORE matching — afterwards the levels this
            # order filled against are gone and the book shows a different market
            arrival_bid = app.book.best_bid()
            arrival_ask = app.book.best_ask()
            # register ownership first: this order may be one side of its own fills
            app.owners[order_id] = owner
            trades = app.book.add_order(order)
            attribute(trades)
            # A limit order's remainder rests; a market order's remainder is
            # cancelled outright. Same number, different meaning — so say which.
            resting = order_id in app.book.order_id_to_order
            position = position_to_dict(position_for(owner), mark_price())
            pending = journal_rows(trades, order_id, arrival_bid, arrival_ask)

        # Write outside the lock: a commit is a disk flush, and holding the
        # matching lock across it would stall every other order behind it.
        for row in pending:
            app.journal.record_fill(*row)

        return (
            jsonify(
                order_id=order_id,
                owner=owner,
                filled_quantity=order.original_quantity - order.remaining_quantity,
                remaining_quantity=order.remaining_quantity,
                resting=resting,
                trades=[trade_to_dict(t) for t in trades],
                position=position,
            ),
            201,
        )

    @app.get("/orders/<int:order_id>")
    def get_order(order_id):
        """Only orders still working are returned; filled or cancelled ones 404."""
        with app.book_lock:
            order = app.book.order_id_to_order.get(order_id)
            if order is None:
                return jsonify(error="order not found"), 404
            return jsonify(order_to_dict(order))

    @app.get("/orders")
    def list_orders():
        """Bulk working-order lookup: /orders?ids=3,7,9. Unknown ids are omitted."""
        raw = request.args.get("ids", "")
        if not raw.strip():
            return jsonify([])
        try:
            ids = [int(part) for part in raw.split(",") if part.strip()]
        except ValueError:
            return jsonify(error="ids must be a comma-separated list of integers"), 400

        with app.book_lock:
            index = app.book.order_id_to_order
            found = [index[i] for i in ids if i in index]
            return jsonify([order_to_dict(o) for o in found])

    @app.delete("/orders/<int:order_id>")
    def cancel_order(order_id):
        with app.book_lock:
            cancelled = app.book.cancel_order(order_id)
        if not cancelled:
            return jsonify(error="order not found"), 404
        return jsonify(order_id=order_id, cancelled=True)

    @app.get("/book/best")
    def best():
        with app.book_lock:
            return jsonify(
                best_bid=app.book.best_bid(),
                best_ask=app.book.best_ask(),
                spread=app.book.spread(),
            )

    @app.get("/book/depth")
    def depth():
        with app.book_lock:
            return jsonify(bids=app.book.bid_depth(), asks=app.book.ask_depth())

    @app.get("/book/snapshot")
    def snapshot():
        """Everything the UI needs in one round-trip, consistent under one lock.

        Pass ``working=3,7,9`` to have those orders' live state returned too, so a
        client rendering a ladder needs one request per frame rather than two.
        """
        try:
            trade_limit = int(request.args.get("trades", 25))
        except ValueError:
            return jsonify(error="trades must be an integer"), 400

        raw_working = request.args.get("working", "")
        try:
            working_ids = [p for p in raw_working.split(",") if p.strip()]
            working_ids = [int(p) for p in working_ids]
        except ValueError:
            return jsonify(error="working must be a comma-separated list of integers"), 400

        with app.book_lock:
            book = app.book
            log = book.trade_log
            recent = log[-trade_limit:] if trade_limit > 0 else []

            # one pass for both aggregates; volume_at_price is the volume profile
            # the ladder draws alongside each price level
            volume_at_price = {}
            total_volume = 0
            session_high = session_low = None
            for t in log:
                volume_at_price[t.price] = volume_at_price.get(t.price, 0) + t.quantity
                total_volume += t.quantity
                if session_high is None or t.price > session_high:
                    session_high = t.price
                if session_low is None or t.price < session_low:
                    session_low = t.price

            return jsonify(
                best_bid=book.best_bid(),
                best_ask=book.best_ask(),
                spread=book.spread(),
                bids=book.bid_depth(),
                asks=book.ask_depth(),
                last_price=log[-1].price if log else None,
                prev_price=log[-2].price if len(log) > 1 else None,
                session_high=session_high,
                session_low=session_low,
                trade_count=len(log),
                total_volume=total_volume,
                volume_at_price=volume_at_price,
                working_orders=[
                    order_to_dict(book.order_id_to_order[i])
                    for i in working_ids
                    if i in book.order_id_to_order
                ],
                position=position_to_dict(
                    read_position(request.args.get("owner", "me")),
                    log[-1].price if log else None,
                ),
                recent_trades=[trade_to_dict(t) for t in recent],
            )

    @app.get("/trades")
    def trades():
        limit = request.args.get("limit")
        with app.book_lock:
            log = app.book.trade_log
            if limit is not None:
                try:
                    log = log[-int(limit):]
                except ValueError:
                    return jsonify(error="limit must be an integer"), 400
            return jsonify([trade_to_dict(t) for t in log])

    @app.get("/positions/<owner>")
    def get_position(owner):
        with app.book_lock:
            return jsonify(owner=owner, **position_to_dict(read_position(owner), mark_price()))

    @app.get("/positions")
    def list_positions():
        with app.book_lock:
            mark = mark_price()
            return jsonify(
                {o: position_to_dict(p, mark) for o, p in app.positions.items()}
            )

    @app.post("/book/seed")
    def seed():
        """Build a two-sided book in one call.

        Seeding over individual POSTs costs one HTTP round-trip per order, which
        is slow enough that a client visibly stalls before it can start trading.
        Doing it under a single lock acquisition is effectively instant.
        """
        data = request.get_json(silent=True) or {}
        try:
            mid = int(data.get("mid", 100))
            levels = int(data.get("levels", 7))
            min_size = int(data.get("min_size", 10))
            max_size = int(data.get("max_size", 70))
        except (TypeError, ValueError):
            return jsonify(error="mid, levels, min_size and max_size must be integers"), 400

        if not 1 <= levels <= 50:
            return jsonify(error="levels must be between 1 and 50"), 400
        if mid - levels < 1:
            return jsonify(error="mid is too low for that many levels"), 400
        if min_size < 1 or max_size < min_size:
            return jsonify(error="require 1 <= min_size <= max_size"), 400

        owner = str(data.get("owner", "flow"))

        created = []
        with app.book_lock:
            for i in range(1, levels + 1):
                for side, price in ((Side.BUY, mid - i), (Side.SELL, mid + i)):
                    order_id = next(app.order_id_counter)
                    app.owners[order_id] = owner
                    app.book.add_order(
                        Order(
                            order_id=order_id,
                            side=side,
                            price=price,
                            order_type=OrderType.LIMIT,
                            original_quantity=random.randint(min_size, max_size),
                        )
                    )
                    created.append(order_id)
        # seeded levels straddle the mid, so nothing crosses and nothing fills

        return jsonify(created=created, count=len(created), owner=owner), 201

    @app.post("/book/reset")
    def reset():
        # A reset wipes the book and your P&L, so it is the natural boundary of a
        # practice session: close the old one out and open a new one.
        with app.book_lock:
            app.book = OrderBook()
            app.order_id_counter = count(1)
            app.owners.clear()
            app.positions.clear()
            finished = app.session_id

        app.journal.end_session(finished)
        app.session_id = app.journal.start_session()

        return jsonify(status="reset", ended_session=finished, session_id=app.session_id)

    @app.get("/journal/session")
    def journal_session():
        row = app.journal.query(
            "SELECT id, started_at, ended_at FROM sessions WHERE id = ?", (app.session_id,)
        )[0]
        counted = app.journal.query(
            "SELECT COUNT(*) AS n FROM fills WHERE session_id = ?", (app.session_id,)
        )[0]["n"]
        return jsonify(
            session_id=row["id"], started_at=row["started_at"],
            ended_at=row["ended_at"], fills=counted,
        )

    @app.get("/journal/stats")
    def journal_stats():
        """Session summary. `?all=1` scores every session ever recorded."""
        if request.args.get("all"):
            fills = app.journal.query("SELECT * FROM fills ORDER BY id")
            scope = "all"
        else:
            fills = app.journal.query(
                "SELECT * FROM fills WHERE session_id = ? ORDER BY id", (app.session_id,)
            )
            scope = "session"

        return jsonify(scope=scope, session_id=app.session_id, **session_stats(fills))

    @app.get("/journal/fills")
    def journal_fills():
        """This session's fills, oldest first. `?all=1` for every session."""
        try:
            limit_n = int(request.args.get("limit", 200))
        except ValueError:
            return jsonify(error="limit must be an integer"), 400

        if request.args.get("all"):
            rows = app.journal.query("SELECT * FROM fills ORDER BY id DESC LIMIT ?", (limit_n,))
        else:
            rows = app.journal.query(
                "SELECT * FROM fills WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (app.session_id, limit_n),
            )
        return jsonify(list(reversed(rows)))

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
