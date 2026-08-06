"""Throughput and latency benchmarks for the limit order book.

The headline numbers measure the matching engine **in-process**. Going through
HTTP would mostly measure Werkzeug: the Flask dev server costs milliseconds per
request while a single `add_order` costs microseconds, so an end-to-end number
understates the engine by three orders of magnitude. The API layer is measured
separately at the end, clearly labelled, because it answers a different question.

Usage:
    python benchmarks/benchmark.py
    python benchmarks/benchmark.py --quick
    python benchmarks/benchmark.py --json results.json
"""

import argparse
import json
import random
import sys
import time

from order_book.book import OrderBook
from order_book.enums import OrderType, Side
from order_book.order import Order

NS_PER_S = 1_000_000_000


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def percentiles(samples_ns):
    """Latency distribution. For a matching engine the tail matters more than
    the mean - a p99 that is 50x the median means occasional stalls."""
    ordered = sorted(samples_ns)
    n = len(ordered)

    def at(fraction):
        return ordered[min(n - 1, int(n * fraction))]

    return {
        "p50_us": at(0.50) / 1000,
        "p95_us": at(0.95) / 1000,
        "p99_us": at(0.99) / 1000,
        "max_us": ordered[-1] / 1000,
    }


def summarise(name, elapsed_ns, latencies, extra=None):
    ops = len(latencies)
    result = {
        "scenario": name,
        "operations": ops,
        "seconds": elapsed_ns / NS_PER_S,
        "ops_per_sec": ops / (elapsed_ns / NS_PER_S),
        "mean_us": (elapsed_ns / ops) / 1000,
        **percentiles(latencies),
    }
    if extra:
        result.update(extra)
    return result


def limit(order_id, side, price, quantity):
    return Order(
        order_id=order_id,
        side=side,
        price=price,
        order_type=OrderType.LIMIT,
        original_quantity=quantity,
    )


def market(order_id, side, quantity):
    return Order(
        order_id=order_id,
        side=side,
        price=None,
        order_type=OrderType.MARKET,
        original_quantity=quantity,
    )


# --------------------------------------------------------------------------
# scenarios
# --------------------------------------------------------------------------

def bench_order_construction(n, rng):
    """How much of a submission is object creation rather than matching?

    `Order.__init__` calls time.time_ns() and runs four validation checks, so it
    is not free — worth knowing before attributing all the cost to the book.
    """
    latencies = []
    start = time.perf_counter_ns()
    for i in range(1, n + 1):
        t = time.perf_counter_ns()
        limit(i, Side.BUY, rng.randint(1, 1000), rng.randint(1, 100))
        latencies.append(time.perf_counter_ns() - t)
    return summarise("Order construction", time.perf_counter_ns() - start, latencies)


def bench_resting_inserts(n, rng):
    """Best case: every order rests, nothing ever crosses. Pure insert path —
    heap push on a new level, deque append on an existing one."""
    book = OrderBook()
    orders = [limit(i, Side.BUY, rng.randint(1, 500), rng.randint(1, 100)) for i in range(1, n + 1)]

    latencies = []
    start = time.perf_counter_ns()
    for order in orders:
        t = time.perf_counter_ns()
        book.add_order(order)
        latencies.append(time.perf_counter_ns() - t)
    elapsed = time.perf_counter_ns() - start

    return summarise("Resting inserts (no matching)", elapsed, latencies,
                     {"resting_orders": len(book.order_id_to_order)})


def bench_crossing(n, rng):
    """Every incoming order crosses and fills. Exercises the matching loop,
    price-level cleanup and the lazy-deletion heap path."""
    book = OrderBook()
    next_id = 1
    # deep resting book on the ask side, one order per level, plenty of size
    for price in range(100, 100 + n):
        book.add_order(limit(next_id, Side.SELL, price, 100))
        next_id += 1

    incoming = [limit(next_id + i, Side.BUY, 100 + n, rng.randint(1, 100)) for i in range(n)]

    latencies = []
    start = time.perf_counter_ns()
    for order in incoming:
        t = time.perf_counter_ns()
        book.add_order(order)
        latencies.append(time.perf_counter_ns() - t)
    elapsed = time.perf_counter_ns() - start

    return summarise("Crossing orders (every one matches)", elapsed, latencies,
                     {"trades_generated": len(book.trade_log)})


def bench_market_sweeps(n, rng):
    """Market orders that sweep several price levels each — the heaviest single
    operation the engine performs."""
    book = OrderBook()
    next_id = 1
    for price in range(100, 100 + n):
        book.add_order(limit(next_id, Side.SELL, price, 20))
        next_id += 1

    incoming = [market(next_id + i, Side.BUY, 100) for i in range(n // 10)]

    latencies = []
    start = time.perf_counter_ns()
    for order in incoming:
        t = time.perf_counter_ns()
        book.add_order(order)
        latencies.append(time.perf_counter_ns() - t)
    elapsed = time.perf_counter_ns() - start

    return summarise("Market sweeps (multi-level)", elapsed, latencies,
                     {"trades_generated": len(book.trade_log)})


def bench_cancels(n, rng):
    """Cancellation: O(1) to locate via the id index, O(k) to remove from the
    deque at that price level."""
    book = OrderBook()
    ids = []
    for i in range(1, n + 1):
        book.add_order(limit(i, Side.BUY, rng.randint(1, 500), rng.randint(1, 100)))
        ids.append(i)
    rng.shuffle(ids)

    latencies = []
    start = time.perf_counter_ns()
    for order_id in ids:
        t = time.perf_counter_ns()
        book.cancel_order(order_id)
        latencies.append(time.perf_counter_ns() - t)
    elapsed = time.perf_counter_ns() - start

    return summarise("Cancels", elapsed, latencies,
                     {"orders_left": len(book.order_id_to_order)})


def bench_mixed(n, rng):
    """The headline number: a realistic mix of passive quoting, aggressive
    orders that cross, and cancels, around a drifting mid."""
    book = OrderBook()
    resting = []
    mid = 1000
    next_id = 1

    latencies = []
    start = time.perf_counter_ns()
    for _ in range(n):
        roll = rng.random()

        if roll < 0.10 and resting:
            order_id = resting.pop(rng.randrange(len(resting)))
            t = time.perf_counter_ns()
            book.cancel_order(order_id)
            latencies.append(time.perf_counter_ns() - t)
            continue

        if roll < 0.35:
            # aggressive: cross the spread
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            touch = book.best_ask() if side == Side.BUY else book.best_bid()
            price = touch if touch is not None else mid
            order = limit(next_id, side, price, rng.randint(1, 50))
        else:
            # passive: quote a few ticks off the mid
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            offset = rng.randint(1, 8)
            price = mid - offset if side == Side.BUY else mid + offset
            order = limit(next_id, side, max(1, price), rng.randint(1, 80))

        next_id += 1
        t = time.perf_counter_ns()
        book.add_order(order)
        latencies.append(time.perf_counter_ns() - t)

        if order.remaining_quantity > 0:
            resting.append(order.order_id)
        mid += rng.choice((-1, 0, 0, 1))
        mid = max(50, mid)

    elapsed = time.perf_counter_ns() - start
    return summarise("Mixed realistic flow", elapsed, latencies,
                     {"trades_generated": len(book.trade_log),
                      "resting_orders": len(book.order_id_to_order)})


def bench_depth_scaling(sizes, probe, rng):
    """Does insert cost grow with book size? The design claims O(log n) on the
    heap and O(1) on the dict, so throughput should degrade gently, not linearly.
    """
    rows = []
    for size in sizes:
        book = OrderBook()
        next_id = 1
        for _ in range(size):
            book.add_order(limit(next_id, Side.BUY, rng.randint(1, 20000), rng.randint(1, 100)))
            next_id += 1

        probes = [limit(next_id + i, Side.BUY, rng.randint(1, 20000), 10) for i in range(probe)]
        latencies = []
        start = time.perf_counter_ns()
        for order in probes:
            t = time.perf_counter_ns()
            book.add_order(order)
            latencies.append(time.perf_counter_ns() - t)
        elapsed = time.perf_counter_ns() - start

        row = summarise(f"insert into {size:,}-order book", elapsed, latencies)
        row["book_size"] = size
        row["price_levels"] = len(book.bid_prices_to_orders)
        rows.append(row)
    return rows


def bench_api(n):
    """The HTTP layer, via Flask's test client — no sockets, but real routing,
    JSON parsing and lock acquisition. Included to show the cost the API adds on
    top of the engine, NOT as a measure of the engine itself."""
    try:
        from order_book.api import create_app
    except ImportError:
        return None

    # in-memory journal and no feed: this measures the HTTP layer, not startup
    app = create_app(journal_path=":memory:", data_dir="/nonexistent-benchmark-feeds")
    app.testing = True
    client = app.test_client()
    client.post("/book/seed", json={"mid": 100, "levels": 20})

    latencies = []
    start = time.perf_counter_ns()
    for i in range(n):
        side = "buy" if i % 2 == 0 else "sell"
        t = time.perf_counter_ns()
        client.post("/orders", json={"side": side, "price": 100, "quantity": 5, "owner": "bench"})
        latencies.append(time.perf_counter_ns() - t)
    elapsed = time.perf_counter_ns() - start

    return summarise("POST /orders (Flask test client)", elapsed, latencies)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

HEADER = (f"{'scenario':38} {'ops':>9} {'ops/sec':>12} {'mean us':>9} {'p50 us':>8} "
          f"{'p99 us':>9} {'max us':>9}")


def print_row(row):
    print(
        f"{row['scenario']:38} {row['operations']:>9,} {row['ops_per_sec']:>12,.0f} "
        f"{row['mean_us']:>8.2f} {row['p50_us']:>7.2f} {row['p99_us']:>8.2f} {row['max_us']:>8.1f}"
    )


def main():
    parser = argparse.ArgumentParser(description="Benchmark the limit order book engine.")
    parser.add_argument("--quick", action="store_true", help="smaller workloads, faster run")
    parser.add_argument("--seed", type=int, default=20260806, help="RNG seed for reproducibility")
    parser.add_argument("--json", metavar="PATH", help="also write results as JSON")
    parser.add_argument("--skip-api", action="store_true", help="engine only")
    args = parser.parse_args()

    n = 20_000 if args.quick else 100_000
    depth_sizes = (1_000, 10_000) if args.quick else (1_000, 10_000, 50_000, 200_000)
    depth_probe = 2_000 if args.quick else 10_000

    rng = random.Random(args.seed)

    print()
    print(f"limit order book - engine benchmark   (python {sys.version.split()[0]}, seed {args.seed})")
    print("=" * 100)
    print(HEADER)
    print("-" * 100)

    results = [
        bench_order_construction(n, rng),
        bench_resting_inserts(n, rng),
        bench_crossing(n // 2, rng),
        bench_market_sweeps(n // 2, rng),
        bench_cancels(n, rng),
        bench_mixed(n, rng),
    ]
    for row in results:
        print_row(row)

    print()
    print("book-size scaling - is insertion really sub-linear?")
    print("-" * 100)
    scaling = bench_depth_scaling(depth_sizes, depth_probe, rng)
    for row in scaling:
        print_row(row)

    baseline = scaling[0]["ops_per_sec"]
    worst = scaling[-1]["ops_per_sec"]
    growth = scaling[-1]["book_size"] / scaling[0]["book_size"]
    print(
        f"\n  book grew {growth:,.0f}x  ->  throughput changed {worst / baseline:.2f}x "
        f"({baseline:,.0f} -> {worst:,.0f} ops/sec)"
    )

    api_row = None
    if not args.skip_api:
        api_row = bench_api(min(n, 20_000))
        if api_row:
            print()
            print("HTTP layer (measures Flask, not the engine - see module docstring)")
            print("-" * 100)
            print_row(api_row)
            engine = next(r for r in results if r["scenario"] == "Mixed realistic flow")
            print(
                f"\n  the API adds ~{api_row['mean_us'] - engine['mean_us']:,.0f}us per order "
                f"on top of {engine['mean_us']:.2f}us of matching "
                f"({api_row['mean_us'] / engine['mean_us']:.0f}x overhead)"
            )

    print()
    headline = next(r for r in results if r["scenario"] == "Mixed realistic flow")
    print("=" * 100)
    print(f"  HEADLINE: {headline['ops_per_sec']:,.0f} orders/sec sustained on mixed flow "
          f"(p99 {headline['p99_us']:.1f}us)")
    print("=" * 100)
    print()

    if args.json:
        payload = {
            "python": sys.version.split()[0],
            "seed": args.seed,
            "scenarios": results,
            "depth_scaling": scaling,
            "api": api_row,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"wrote {args.json}\n")


if __name__ == "__main__":
    main()
