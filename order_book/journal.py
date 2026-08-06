import sqlite3
import threading
import time
from order_book.enums import Side

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    timestamp INTEGER NOT NULL,
    side TEXT NOT NULL,
    price INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    best_bid INTEGER,
    best_ask INTEGER, 
    aggressor INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS marks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    timestamp  INTEGER NOT NULL,
    price      INTEGER NOT NULL
);
"""

class Journal:
    def __init__(self, path="journal.db"):
        # check_same_thread=False because the web server answers each request on
        # a different thread, and sqlite3 otherwise refuses a connection made on
        # another one. That is only safe because every access below is
        # serialised through _lock — the connection itself is not thread-safe.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def start_session(self):
        with self._lock:
            self.db.execute("INSERT INTO sessions (started_at) VALUES (?)", (time.time_ns(),),)
            session_id = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.db.commit()
            return session_id

    def record_fill(self, session_id, timestamp, side, price, quantity, order_id, best_bid, best_ask, aggressor):
        with self._lock:
            self.db.execute("""INSERT INTO fills (session_id, timestamp, side, price, quantity, order_id, best_bid, best_ask, aggressor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (session_id, timestamp, side, price, quantity, order_id, best_bid, best_ask, aggressor),)
            self.db.commit()

    def record_mark(self, session_id, timestamp, price):
        with self._lock:
            self.db.execute("""INSERT INTO marks (session_id, timestamp, price) VALUES (?, ?, ?)""",(session_id, timestamp, price),)
            self.db.commit()

    def end_session(self, session_id):
        with self._lock:
            self.db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (time.time_ns(), session_id),)
            self.db.commit()

    def query(self, sql, params=()):
        """Read rows as dicts. Callers should use this rather than reaching for
        .db directly, so reads are serialised like everything else."""
        with self._lock:
            cursor = self.db.execute(sql, params)
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def clear_history(self):
        with self._lock:
            self.db.execute("DELETE FROM marks")
            self.db.execute("DELETE FROM fills")
            self.db.execute("DELETE FROM sessions")
            self.db.commit()