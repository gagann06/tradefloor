import sqlite3
import threading

import pytest

from order_book.journal import Journal


@pytest.fixture
def journal():
    return Journal(":memory:")


def fill(journal, session_id, **overrides):
    args = dict(
        timestamp=1_000,
        side="buy",
        price=103,
        quantity=10,
        order_id=7,
        best_bid=102,
        best_ask=103,
        aggressor=1,
    )
    args.update(overrides)
    journal.record_fill(session_id, **args)


# --------------------------- schema ---------------------------

def test_schema_is_created_on_construction(journal):
    tables = {row[0] for row in journal.db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )}

    assert {"sessions", "fills"} <= tables


def test_reopening_an_existing_database_is_safe(tmp_path):
    path = tmp_path / "journal.db"
    first = Journal(str(path))
    session_id = first.start_session()
    fill(first, session_id)

    # CREATE TABLE IF NOT EXISTS must not wipe or fail on an existing file
    second = Journal(str(path))

    assert second.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


def test_fills_survive_the_process_that_wrote_them(tmp_path):
    path = tmp_path / "journal.db"
    writer = Journal(str(path))
    session_id = writer.start_session()
    fill(writer, session_id, price=101)
    writer.end_session(session_id)
    writer.db.close()

    reopened = sqlite3.connect(str(path))

    assert reopened.execute("SELECT price FROM fills").fetchone()[0] == 101


# --------------------------- sessions ---------------------------

def test_start_session_returns_a_new_id(journal):
    assert journal.start_session() is not None


def test_each_session_gets_a_distinct_id(journal):
    assert journal.start_session() != journal.start_session()


def test_a_running_session_has_no_end_time(journal):
    session_id = journal.start_session()

    row = journal.db.execute(
        "SELECT started_at, ended_at FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()

    assert row[0] > 0
    assert row[1] is None


def test_end_session_stamps_only_that_session(journal):
    first = journal.start_session()
    second = journal.start_session()

    journal.end_session(first)

    ended = dict(journal.db.execute("SELECT id, ended_at FROM sessions"))
    assert ended[first] is not None
    assert ended[second] is None      # the WHERE clause must not close everything


# --------------------------- fills ---------------------------

def test_record_fill_stores_every_column(journal):
    session_id = journal.start_session()

    journal.record_fill(session_id, 1234, "sell", 99, 5, 42, 98, 99, 0)

    row = journal.db.execute(
        """SELECT session_id, timestamp, side, price, quantity,
                  order_id, best_bid, best_ask, aggressor FROM fills"""
    ).fetchone()

    assert row == (session_id, 1234, "sell", 99, 5, 42, 98, 99, 0)


def test_fills_are_tied_to_their_session(journal):
    monday = journal.start_session()
    friday = journal.start_session()
    fill(journal, monday, price=100)
    fill(journal, friday, price=200)
    fill(journal, friday, price=201)

    counts = dict(journal.db.execute(
        "SELECT session_id, COUNT(*) FROM fills GROUP BY session_id"
    ))

    assert counts == {monday: 1, friday: 2}


def test_a_one_sided_book_records_a_null_touch(journal):
    """A market buy can fill with nothing bidding behind it."""
    session_id = journal.start_session()

    fill(journal, session_id, best_bid=None)

    assert journal.db.execute("SELECT best_bid FROM fills").fetchone()[0] is None


def test_aggressor_is_required(journal):
    session_id = journal.start_session()

    with pytest.raises(sqlite3.IntegrityError):
        fill(journal, session_id, aggressor=None)


def test_price_is_required(journal):
    session_id = journal.start_session()

    with pytest.raises(sqlite3.IntegrityError):
        fill(journal, session_id, price=None)


# --------------------------- threads ---------------------------

def test_can_be_used_from_another_thread(journal):
    """A web server answers each request on a different thread, and sqlite3
    refuses a connection made on another one unless told otherwise. Nothing
    single-threaded catches this."""
    session_id = journal.start_session()
    errors = []

    def write():
        try:
            fill(journal, session_id)
        except Exception as exc:            # pragma: no cover - only on regression
            errors.append(exc)

    worker = threading.Thread(target=write)
    worker.start()
    worker.join()

    assert errors == []
    assert journal.query("SELECT COUNT(*) AS n FROM fills")[0]["n"] == 1


def test_concurrent_writers_do_not_lose_or_corrupt_rows(journal):
    session_id = journal.start_session()
    errors = []

    def write_many():
        try:
            for _ in range(25):
                fill(journal, session_id)
        except Exception as exc:            # pragma: no cover - only on regression
            errors.append(exc)

    workers = [threading.Thread(target=write_many) for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert errors == []
    assert journal.query("SELECT COUNT(*) AS n FROM fills")[0]["n"] == 100


def test_query_returns_dicts_keyed_by_column(journal):
    session_id = journal.start_session()
    fill(journal, session_id, price=107, side="sell")

    rows = journal.query("SELECT side, price FROM fills")

    assert rows == [{"side": "sell", "price": 107}]


# --------------------------- what it is all for ---------------------------

def test_spread_paid_can_be_queried(journal):
    """The question the journal exists to answer."""
    session_id = journal.start_session()
    # crossed: bought the 103 offer in a 102/103 market -> half a tick over mid
    fill(journal, session_id, side="buy", price=103, best_bid=102, best_ask=103, aggressor=1)
    # crossed again, wider market: bought 105 in a 101/105 -> two ticks over mid
    fill(journal, session_id, side="buy", price=105, best_bid=101, best_ask=105, aggressor=1)
    # passive fill, should be excluded from the crossing figure
    fill(journal, session_id, side="buy", price=100, best_bid=100, best_ask=101, aggressor=0)

    overpay = journal.db.execute(
        """SELECT AVG(price - (best_bid + best_ask) / 2.0)
           FROM fills WHERE side = 'buy' AND aggressor = 1"""
    ).fetchone()[0]

    assert overpay == pytest.approx(1.25)     # (0.5 + 2.0) / 2


def test_null_touch_is_skipped_not_counted_as_zero(journal):
    session_id = journal.start_session()
    fill(journal, session_id, side="buy", price=103, best_bid=102, best_ask=103, aggressor=1)
    fill(journal, session_id, side="buy", price=99, best_bid=None, best_ask=99, aggressor=1)

    overpay = journal.db.execute(
        """SELECT AVG(price - (best_bid + best_ask) / 2.0)
           FROM fills WHERE side = 'buy' AND aggressor = 1"""
    ).fetchone()[0]

    assert overpay == pytest.approx(0.5)      # the NULL row must not drag it toward zero
