# Tradefloor

A limit order book matching engine in Python, with a live depth-of-market trading
terminal, position and P&L tracking - including a benchmark suite.

The engine implements **price-time priority**: orders match against the best
available price first, and within a price level in the order they arrived.
Everything above it — the REST API, the browser terminal, the synthetic market —
exists to exercise and demonstrate that core.

```
~125,000 orders/sec sustained on mixed flow, p99 latency 18µs
insert throughput flat across a 200x increase in book size
126 tests
```

---

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
pip install -e .
```

Run the tests:

```bash
pytest
```

Start the trading terminal at <http://localhost:5000> once VS Code Live Server started:

```bash
flask --app order_book.api:create_app run --debug
```

Benchmark the engine:

```bash
python benchmarks/benchmark.py
```

Requires Python 3.10 or newer.

---

## The trading terminal

Press **Start flow** to bring the market to life, then trade into it.

The ladder follows the MD Trader / Jigsaw convention used by discretionary
futures traders: price fixed in a centre column, bid depth to the left, ask depth
to the right, so a price level stays in the same place as the market moves.

| column | meaning |
|---|---|
| **Vol** | cumulative traded volume at that price (volume profile) |
| **Buy / Sell** | *your* working orders — click to cancel them at that price |
| **Bid / Ask** | resting depth — click to place an order there |
| **Price** | last trade highlighted amber, session high/low marked |

Order entry supports click-to-trade on the ladder, typed limit prices, market
orders, flatten, and cancel-all. Keyboard: `B` buy, `S` sell, `F` flatten,
`Esc` cancel all, `C` re-centre, `1`–`4` size presets.

Position, average entry, realised and unrealised P&L update live, marked against
the last traded price.

### The synthetic market

Order flow is generated in the browser and submitted through the **real REST
API** — nothing bypasses the engine.

Price is not scripted. The generator only decides what orders to send; the price
is whatever the book prints as a result. Sustained buying eats the offers and the
market rises on its own. Flow moves between regimes (balanced, grind up, grind
down, squeeze, flush, quiet) that persist for tens of seconds, which is what
produces trends, pullbacks and consolidation rather than noise.

---

## Design notes

The decisions worth explaining, and why they were made that way.

### Price levels: a dict *and* a heap

Each side keeps a dict of `price -> deque` for O(1) access to any level, plus a
heap of prices for the best bid or offer. Bids negate their prices so Python's
min-heap yields the highest bid.

Emptied levels are removed from the dict but left in the heap — `heapq` has no
cheap arbitrary removal. `best_bid()` and `best_ask()` peek the top and discard
any price no longer present in the dict. Lazy deletion keeps every operation
O(log n) instead of paying O(n) to rebuild the heap.

### FIFO within a level

Each level is a `collections.deque`, not a list. Both support append and
pop-from-front, but `list.pop(0)` is O(n) because every remaining element shifts.
Matching pops from the front on every fill, so that difference compounds.

### An order-id index

`order_id -> Order` makes cancellation a direct lookup rather than a scan across
every price level. Removal from the deque itself is still O(k) in the level's
depth — no built-in structure gives O(1) arbitrary removal — which is an
acceptable trade when cancels are rarer than fills.

### Market orders never rest

A market order carries no price at all: the constructor *rejects* one that does,
and requires one for limit orders, so neither nonsensical combination can be
built. Its unfilled remainder is cancelled rather than rested — and kept out of
the id index, so no ghost order survives that a later cancel could trip over.

### P&L is not the engine's job

An order book matches orders; it has no concept of who owns them. Ownership and
P&L live in the API layer, and `Position` is a separate module.

Accounting is **average cost**: a fill that increases a position rolls the cost
basis forward, one that reduces it realises against that average, and one that
flips through flat closes the old position and rebases the new one at the fill
price. Signed quantity means long and short need no separate code paths —
`(mark - average) * quantity` gives the correct sign for both.

The subtle part is attribution. A trade is only ever returned from the
*aggressor's* `add_order` call, so when your resting bid is lifted, that fill
surfaces in someone else's API request. Both sides of every fill are attributed
from that one result.

### The engine is single-threaded on purpose

Real matching engines are, for determinism. Concurrency is the API layer's
problem, so the Flask layer serialises access behind a lock and the engine stays
lock-free.

---

## API

| method | path | |
|---|---|---|
| `POST` | `/orders` | submit an order; returns fills, resting state and position |
| `GET` | `/orders/<id>` | a working order's live state (404 once filled) |
| `GET` | `/orders?ids=1,2,3` | bulk working-order lookup |
| `DELETE` | `/orders/<id>` | cancel |
| `GET` | `/book/snapshot` | whole screen in one locked round-trip |
| `GET` | `/book/best` | best bid, best ask, spread |
| `GET` | `/book/depth` | resting quantity per price level |
| `GET` | `/trades` | trade history |
| `GET` | `/positions` · `/positions/<owner>` | position and P&L |
| `POST` | `/book/seed` | build a two-sided book in one call |
| `POST` | `/book/reset` | clear book, positions and ids |
| `GET` | `/health` | liveness |

`/book/snapshot` accepts `?owner=`, `?working=1,2,3` and `?trades=N` so a client
rendering a ladder needs one request per frame rather than several.

---

## Benchmarks

Measured **in-process**. Routing through HTTP would mostly measure Werkzeug — the
API costs ~470µs per order against ~8µs of matching — so an end-to-end figure
understates the engine by roughly 60x. The HTTP layer is reported separately.

CPython 3.14, single run:

| scenario | ops/sec | mean | p50 | p99 |
|---|---:|---:|---:|---:|
| Order construction | 381,112 | 2.62µs | 2.00µs | 5.30µs |
| Resting inserts (no matching) | 578,522 | 1.73µs | 1.20µs | 3.10µs |
| Crossing orders (every one matches) | 204,479 | 4.89µs | 4.50µs | 14.20µs |
| Market sweeps (multi-level) | 57,209 | 17.48µs | 14.00µs | 40.50µs |
| Cancels | 234,617 | 4.26µs | 2.60µs | 16.60µs |
| **Mixed realistic flow** | **125,194** | **7.99µs** | 2.60µs | 17.90µs |

Insert throughput against book size — the evidence that the data structures hold
up, rather than an assertion that they do:

| book size | ops/sec |
|---:|---:|
| 1,000 | 448,716 |
| 10,000 | 452,509 |
| 50,000 | 464,100 |
| 200,000 | 449,462 |

The book grew **200x** and throughput was unchanged. A naive sorted-list
implementation would degrade visibly here.

One incidental finding: constructing an `Order` (2.62µs) costs more than
inserting it into the book (1.73µs) — `time.time_ns()` and validation, not
matching.

These are single-machine, single-run numbers with no repeat trials. Treat them as
"on my laptop", not a guarantee.

---

## Tests

```
tests/test_order.py       11   fields, validation, limit vs market price rules
tests/test_book.py        23   matching, price-time priority, sweeps, cancels
tests/test_position.py    23   average cost, realised/unrealised, flips
tests/test_api.py         55   endpoints, ownership attribution, error paths
tests/test_benchmark.py   13   harness smoke tests
tests/test_setup.py        1
```

The browser layer has **no automated tests** — `templates/index.html` was
verified by driving the live page, not in the suite.

---

## Layout

```
order_book/
  order.py        the Order model and its validation
  book.py         the matching engine
  trade.py        an execution record
  position.py     average-cost position and P&L
  enums.py        Side, OrderType
  api.py          Flask REST API and ownership tracking
  templates/
    index.html    the trading terminal
benchmarks/
  benchmark.py    throughput and latency harness
tests/
```

---

## Not built yet

- **Trading bots to compare against.** The intended next step: rule-based
  strategies trading the same book, so a human session can be scored against
  them. Ownership and P&L are already per-owner, so the engine side is ready.
- Multiple instruments — one `OrderBook` currently means one symbol.
- Persistence. State is in memory and dies with the process.
- Iceberg orders, stop orders, self-trade prevention.
