# Tradefloor

A limit order book matching engine in Python, wrapped in a depth-of-market
trading terminal that replays real market data, tracks your position and P&L,
and keeps a journal of how you actually traded.

The engine implements **price-time priority**: orders match against the best
available price first, and within a price level in the order they arrived.
Everything above it exists to exercise that core and to make practising against
it useful.

```
~138,000 orders/sec sustained on mixed flow, p99 latency 13us
O(1) cancellation, flat from 1 to 10,000 orders deep at a price
insert throughput flat across a 200x increase in book size
234 tests
```

![The trading terminal: depth-of-market ladder, position and P&L, price chart, time and sales, and the session review](docs/terminal.png)

A live session above. The ladder is on the left with the last trade highlighted,
the review along the bottom, and the verdict reads
`+123.00 at mid  -  33.00 spread paid  =  +90.00 realised`.

---

## Quick start

Requires Python 3.10 or newer, and `git`. On macOS use `python3` and `pip3` if
`python` points at the system Python 2.

Clone the repository and move into it:

```bash
git clone https://github.com/gagann06/tradefloor.git
cd tradefloor
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

**Windows**

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

Run the tests:

```bash
pytest
```

Start the terminal at <http://localhost:5000>:

```bash
tradefloor
```

`pip install -e .` puts that command on your path. `--port`, `--host`,
`--journal` and `--no-debug` are available, and `python -m order_book.api` does
the same thing if you would rather not install the package.

Download some real market data to trade against:

```bash
python scripts/fetch_prices.py stock --random
```

Benchmark the engine:

```bash
python benchmarks/benchmark.py
```

---

## The terminal

Press **Start flow** to bring the market to life, then trade into it.

The ladder follows the MD Trader convention used by discretionary futures
traders: price fixed in a centre column, bid depth to the left, ask depth to the
right, so a price level stays in the same place as the market moves.

| column | meaning |
|---|---|
| **Vol** | cumulative traded volume at that price |
| **Buy / Sell** | *your* working orders. Click to cancel them at that price |
| **Bid / Ask** | resting depth. Click to place an order there |
| **Price** | last trade highlighted, session high and low marked |

Order entry supports click-to-trade on the ladder, typed limit prices, market
orders, flatten, and cancel-all. Keyboard: `B` buy, `S` sell, `F` flatten,
`Esc` cancel all, `C` re-centre, `1` to `4` size presets.

### Real price action

Price comes from downloaded market data, not from a generator. Two sources,
normalised to the same format so nothing downstream cares which:

```bash
python scripts/fetch_prices.py stock --random          # a random liquid ticker
python scripts/fetch_prices.py stock --symbol SPY --range 5d
python scripts/fetch_prices.py crypto --symbol BTCUSDT --date 2026-07-15
```

Stocks and ETFs come from Yahoo at 1-minute resolution; crypto from Binance's
public archive at 1-second. The data is gitignored, so the script is what makes
it reproducible.

**The instrument is hidden until you reveal it.** Knowing you are looking at
TSLA imports assumptions about how it ought to behave, so a session trades an
unlabelled series and the identity is shown afterwards. A reset draws a new one.

The synthetic flow still generates the *orders*, so the book, queue position and
fills are simulated. What is real is the price path.

### Session review

Your P&L has two sources, and only one of them is skill at reading a market. The
review separates them.

**Direction** comes from your round trips: win rate, average win and loss,
expectancy per trade.

**Execution** comes from your fills, each compared against the mid price at the
moment your order arrived: how often you crossed the spread, what crossing cost
you, what resting earned you, and the total spread bill.

The headline is the arithmetic between them:

```
P&L at mid  -  Spread paid  =  Realised
```

A real session: crossing every trade was right about direction (+15 at mid) but
57 of spread turned it into -42. Another, resting orders instead: wrong about
direction (-21 at mid) but passive fills earned 11 back, ending at -10. No win
rate would tell you either of those.

**Excursions** answer a third question: did you sit through it? MAE is how far a
trade went against you before it worked, MFE how far in your favour before you
closed, and capture rate is how much of the available move you actually took. A
trade worth 12 at its best where you took 3 is a 25% capture, which is the
signature of cutting winners short.

History persists across restarts in SQLite, and can be cleared from the review
panel when you want a clean slate.

---

## Design notes

The decisions worth explaining, and why they were made that way.

### Price levels: a dict *and* a heap

Each side keeps a dict of `price -> PriceLevel` for O(1) access to any level,
plus a heap of prices for the best bid or offer. Bids negate their prices so
Python's min-heap yields the highest bid.

Emptied levels are removed from the dict but left in the heap, because `heapq`
has no cheap arbitrary removal. `best_bid()` and `best_ask()` peek the top and
discard any price no longer present in the dict. Lazy deletion keeps every
operation O(log n) instead of paying O(n) to rebuild the heap.

### FIFO within a level

Each level is an intrusive doubly-linked list: orders carry their own `prev` and
`next`, so the list nodes *are* the orders rather than wrappers pointing at them.
Append at the tail, match from the head, which is time priority.

This started as a `collections.deque`, which is the obvious choice and is O(1) at
both ends. The problem is the one operation a deque cannot do: removing from the
middle. A deque has no addressable interior, so `remove()` scans for the position,
and cancellation only ever knows the *order*, never where it sits in the level.
Linking through the orders themselves makes the object its own position.

### An order-id index

`order_id -> Order` makes cancellation a direct lookup rather than a scan across
every price level — and because the order carries its own links, that lookup is
now sufficient. Unlinking is pointer surgery, so cancellation is O(1) at any
depth. Under the deque it was O(1) to *find* and O(k) to *remove*, which is a
distinction worth being precise about: the index was never the slow part.

Note what cancellation does *not* do: touch the heap. Emptying a level drops the
dict key and leaves the price behind for lazy deletion to clear on the next read.
Removing an arbitrary price from a heap would be O(n) to locate, so this is the
decision that keeps cancels cheap — the linked list only removes the second cost.

### Market orders never rest

A market order carries no price at all: the constructor *rejects* one that does,
and requires one for limit orders, so neither nonsensical combination can be
built. Its unfilled remainder is cancelled rather than rested, and kept out of
the id index, so no ghost order survives that a later cancel could trip over.

### P&L is not the engine's job

An order book matches orders; it has no concept of who owns them. Ownership and
P&L live in the API layer, and `Position` is a separate module.

Accounting is **average cost**: a fill that increases a position rolls the cost
basis forward, one that reduces it realises against that average, and one that
flips through flat closes the old position and rebases the new one at the fill
price. Signed quantity means long and short need no separate code paths, since
`(mark - average) * quantity` gives the correct sign for both.

The subtle part is attribution. A trade is only ever returned from the
*aggressor's* `add_order` call, so when your resting bid is lifted, that fill
surfaces in someone else's API request. Both sides are attributed from that one
result.

### The journal stores observations, not conclusions

Fills record what happened; marks record where the price went. Round trips,
excursions and every statistic are **derived** from those two tables rather than
stored alongside them. A stored result is a cache that goes stale the moment you
fix a calculation or invent a new metric, and would need every historical row
migrating to catch up. Derived, a change applies retroactively to every session
already recorded.

Round trips reuse `Position` rather than reimplementing average cost, which
makes the journal agreeing with the P&L panel structural instead of
coincidental. A test asserts the two match across several fill sequences.

### Execution is measured against the arrival price

A fill's spread cost is measured against the mid at the moment the order was
*sent*, captured before `add_order` runs. It cannot be read afterwards, because
matching has already consumed the levels it filled against, so the book then
shows where the market ended up rather than where it was. Reading it after the
fact would understate crossing cost, always in the flattering direction.

### Feeds are scaled by volatility, not by range

Fitting a session into a fixed number of ticks sounds reasonable and is useless.
A day of Bitcoin ranges 1.73%, so forty ticks makes one tick worth $28 against a
typical one-second move of $0.84, and 97% of consecutive points land on the same
tick. Scaling so a *typical move* is about half a tick leaves roughly 60% of
steps flat, which is what a real market looks like at a tradeable resolution.

### The engine is single-threaded on purpose

Real matching engines are, for determinism. Concurrency is the API layer's
problem, so Flask serialises access behind a lock and the engine stays
lock-free. The journal serialises its own connection separately, and opens it
with `check_same_thread=False` because the server answers each request on a
different thread.

---

## API

| method | path | |
|---|---|---|
| `POST` | `/orders` | submit an order. Returns fills, resting state and position |
| `GET` | `/orders/<id>` | a working order's live state, 404 once filled |
| `GET` | `/orders?ids=1,2,3` | bulk working-order lookup |
| `DELETE` | `/orders/<id>` | cancel |
| `GET` | `/book/snapshot` | the whole screen in one locked round-trip |
| `GET` | `/book/best` | best bid, best ask, spread |
| `GET` | `/book/depth` | resting quantity per price level |
| `GET` | `/trades` | trade history |
| `GET` | `/positions` and `/positions/<owner>` | position and P&L |
| `POST` | `/book/seed` | build a two-sided book in one call |
| `POST` | `/book/reset` | clear book, positions and ids, start a session |
| `GET` | `/journal/session` | the current session |
| `GET` | `/journal/fills` | recorded fills, `?all=1` for every session |
| `GET` | `/journal/stats` | session review, `?all=1` for every session |
| `POST` | `/journal/clear` | erase all history. Requires `{"confirm": true}` |
| `GET` | `/feed` | the loaded instrument, `?reveal=1` to name it |
| `GET` | `/feed/prices` | a slice of the price series |
| `GET` | `/feed/list` and `POST /feed/load` | choose a feed |
| `GET` | `/health` | liveness |

`/book/snapshot` accepts `?owner=`, `?working=1,2,3` and `?trades=N`, so a
client rendering a ladder needs one request per frame rather than several.

---

## Benchmarks

Measured **in-process**. Routing through HTTP would mostly measure Werkzeug,
which costs around 400us per order against 7us of matching, so an end-to-end
figure understates the engine by roughly 55x. The HTTP layer is reported
separately.

CPython 3.14, median of 15 trials with the observed range. Scenario order is
shuffled within each trial, so nothing is systematically measured on a cold
interpreter or a warm core:

| scenario | ops/sec | range | p50 | p99 |
|---|---:|---:|---:|---:|
| Order construction | 419,765 | 351,723-434,475 | 1.90us | 4.20us |
| Resting inserts, no matching | 680,031 | 600,627-767,581 | 1.00us | 2.30us |
| Crossing orders, every one matches | 202,414 | 171,168-225,774 | 4.70us | 11.00us |
| Market sweeps, multi-level | 59,249 | 45,263-64,272 | 14.70us | 41.60us |
| Cancels | 529,957 | 325,123-553,907 | 1.40us | 2.70us |
| **Mixed realistic flow** | **138,033** | 113,464-149,391 | 2.30us | 13.10us |
| POST /orders, Flask test client | 2,456 | 2,159-2,567 | 315.40us | 1377.70us |

Mean latency is not listed because it is exactly `1,000,000 / ops_per_sec` and
says nothing the throughput column does not.

### Cancellation against level depth

Live order flow is dominated by cancels, so the cost is worth isolating. Book
size is held at 20,000 resting orders and only the number of distinct prices
changes, which makes depth-per-level the single variable:

| depth per level | deque | doubly-linked list |
|---:|---:|---:|
| 1 | 1.40us | 1.08us |
| 10 | 0.82us | 0.93us |
| 100 | 1.11us | 0.83us |
| 1,000 | 4.61us | 0.96us |
| 10,000 | 35.01us | 0.79us |

Removing from the middle of a deque means scanning it for the position, however
cheap the lookup that found the order was. Orders now carry their own prev/next
links, so the order-ID index is sufficient to unlink and the cost stays flat.

At shallow depths the two are indistinguishable — the deque is marginally ahead
at depth 10, which is measurement noise rather than a real ordering. The change
only matters once levels get deep, and it costs about 1% of mixed-flow
throughput to have it: a paired ratio of 0.987 against the deque implementation,
measured within trials on identical order sequences.

### Insert throughput against book size

| book size | ops/sec | range |
|---:|---:|---:|
| 1,000 | 471,522 | 342,477-560,255 |
| 10,000 | 530,949 | 345,232-560,095 |
| 50,000 | 568,314 | 392,482-602,493 |
| 200,000 | 580,178 | 389,956-637,503 |

The book grew **200x** and throughput did not degrade; the per-trial ratio has a
median of 1.20x. The reason is worth stating rather than presenting the data
structures as magic. Prices are drawn from 1..20,000, and the number of occupied
levels at each book size is 974, 7,866, 18,380 and 19,999 — so a small book opens
a *new* price level on nearly every insert, paying a heap push and a level
allocation, while a book of 200,000 has already occupied all but one of the
available prices and an insert becomes a plain tail append.

What flattens the curve is therefore level saturation against a bounded price
range, not size independence. With unbounded prices the heap would keep growing
and the log n term would keep climbing. The measurement is honest about the
engine; it is not evidence of an insert whose cost never grows.

One incidental finding: constructing an `Order` (p50 1.90us) costs more than
inserting it into the book (p50 1.00us). That is `time.time_ns()` and validation,
not matching.

These are single-machine numbers from a Windows laptop. Trial-to-trial spread
runs 17-43% depending on the scenario, so treat the medians as "on my laptop"
rather than a guarantee, and prefer the paired ratios where a comparison is
being made — those are computed within a trial against an identical order
sequence, so machine drift cancels instead of landing on one side.

---

## Tests

```
tests/test_api.py         89   endpoints, ownership attribution, error paths
tests/test_analysis.py    41   round trips, session stats, excursions
tests/test_book.py        23   matching, price-time priority, sweeps, cancels
tests/test_position.py    23   average cost, realised and unrealised, flips
tests/test_journal.py     17   schema, persistence, threading
tests/test_pricefeed.py   16   rescaling, downsampling, anonymity
tests/test_benchmark.py   13   harness smoke tests
tests/test_order.py       11   fields, validation, limit versus market
tests/test_setup.py        1
```

The browser layer has **no automated tests**. `templates/index.html` was
verified by driving the live page, not in the suite.

---

## Layout

```
order_book/
  order.py        the Order model and its validation
  book.py         the matching engine
  price_level.py  the FIFO queue of orders resting at one price
  trade.py        an execution record
  position.py     average-cost position and P&L
  journal.py      SQLite storage for fills, marks and sessions
  analysis.py     round trips, session statistics, excursions
  pricefeed.py    loading and rescaling real market data
  enums.py        Side, OrderType
  api.py          Flask REST API, ownership, journalling
  templates/
    index.html    the trading terminal
benchmarks/
  benchmark.py    throughput and latency harness
scripts/
  fetch_prices.py download market data
tests/
```

---

## Coming soon

- **Trading bots to compare against.** Rule-based strategies trading the same
  book, so a session can be scored against them. Ownership and P&L are already
  per-owner, so the engine side is ready.
- **Full order-by-order replay.** The price path is real, but the book around it
  is generated. A faithful replay needs L3 market-by-order data.
- Multiple instruments. One `OrderBook` currently means one symbol.
- Iceberg orders, stop orders, self-trade prevention.

---

## License

MIT. See [LICENSE](LICENSE).
