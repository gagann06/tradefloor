"""Download real market data for replay through the simulator.

Two sources, one output format. Everything downstream reads a plain
``timestamp,price`` CSV and does not care where it came from, so adding another
source later means writing one function here and nothing else.

    python scripts/fetch_prices.py crypto --symbol BTCUSDT --date 2026-07-15
    python scripts/fetch_prices.py stock --random
    python scripts/fetch_prices.py stock --symbol SPY --range 5d

The data itself is gitignored — this script is what makes it reproducible.
"""

import argparse
import csv
import io
import json
import pathlib
import random
import sys
import urllib.request
import zipfile

DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data"

# Liquid, heavily traded names. The pool matters: an illiquid ticker gives a
# sparse, gappy series that teaches nothing about reading a market.
TICKER_POOL = [
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK",
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "JPM", "XOM", "WMT", "KO", "PFE", "BA",
]

CRYPTO_POOL = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

USER_AGENT = "Mozilla/5.0 (compatible; tradefloor/0.1; +local simulator)"


def get(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

def fetch_crypto(symbol, date, interval="1s"):
    """Binance's public data archive: static zipped CSVs, no key, no account."""
    url = (
        f"https://data.binance.vision/data/spot/daily/klines/"
        f"{symbol}/{interval}/{symbol}-{interval}-{date}.zip"
    )
    print(f"  GET {url}")
    payload = get(url)
    print(f"  {len(payload):,} bytes")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        text = archive.read(name).decode()

    rows = []
    for line in csv.reader(io.StringIO(text)):
        if not line or not line[0].strip():
            continue
        try:
            # open_time, open, high, low, close, ...  — the close is the price
            # that actually stood at the end of that second
            rows.append((int(float(line[0])), float(line[4])))
        except (ValueError, IndexError):
            continue     # header row or a malformed line; the archive has both

    meta = {"symbol": symbol, "source": "binance", "interval": interval, "day": date}
    return rows, meta


def fetch_stock(symbol, range_="1d", interval="1m"):
    """Yahoo's chart endpoint. Undocumented rather than a published product, so
    treat it as best-effort: it can change or rate-limit without notice."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_}&interval={interval}"
    )
    print(f"  GET {url}")
    payload = json.loads(get(url))

    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    # gaps are normal — a minute with no trade comes back as null
    rows = [(int(t) * 1000, float(c)) for t, c in zip(stamps, closes) if c is not None]

    meta = {
        "symbol": symbol,
        "source": "yahoo",
        "interval": interval,
        "range": range_,
        "exchange": result["meta"].get("fullExchangeName"),
        "currency": result["meta"].get("currency"),
    }
    return rows, meta


# --------------------------------------------------------------------------

def write_feed(rows, meta):
    if len(rows) < 2:
        raise SystemExit("refusing to write a feed with fewer than 2 points")

    DATA_DIR.mkdir(exist_ok=True)
    slug = f"{meta['source']}-{meta['symbol']}-{meta.get('day') or meta.get('range')}"
    csv_path = DATA_DIR / f"{slug}.csv"
    meta_path = DATA_DIR / f"{slug}.json"

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "price"])
        writer.writerows(rows)

    prices = [p for _, p in rows]
    meta = {
        **meta,
        "points": len(rows),
        "first_price": prices[0],
        "last_price": prices[-1],
        "low": min(prices),
        "high": max(prices),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    move = (prices[-1] / prices[0] - 1) * 100
    print(f"\n  wrote {csv_path.name}")
    print(f"  {len(rows):,} points   {min(prices):,.2f} - {max(prices):,.2f}   "
          f"session move {move:+.2f}%")
    return csv_path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="source", required=True)

    crypto = sub.add_parser("crypto", help="Binance public archive (1-second bars)")
    crypto.add_argument("--symbol", default="BTCUSDT")
    crypto.add_argument("--date", required=False, help="YYYY-MM-DD")
    crypto.add_argument("--interval", default="1s", choices=["1s", "1m", "5m"])
    crypto.add_argument("--random", action="store_true", help="pick a symbol at random")

    stock = sub.add_parser("stock", help="Yahoo chart endpoint (1-minute bars)")
    stock.add_argument("--symbol", default="SPY")
    stock.add_argument("--range", dest="range_", default="1d")
    stock.add_argument("--interval", default="1m")
    stock.add_argument("--random", action="store_true", help="pick a ticker at random")

    args = parser.parse_args()

    if args.source == "crypto":
        symbol = random.choice(CRYPTO_POOL) if args.random else args.symbol
        if not args.date:
            raise SystemExit("crypto needs --date YYYY-MM-DD (the archive is daily)")
        print(f"fetching {symbol} {args.interval} for {args.date}")
        rows, meta = fetch_crypto(symbol, args.date, args.interval)
    else:
        symbol = random.choice(TICKER_POOL) if args.random else args.symbol
        print(f"fetching {symbol} {args.interval} over {args.range_}")
        rows, meta = fetch_stock(symbol, args.range_, args.interval)

    write_feed(rows, meta)


if __name__ == "__main__":
    sys.exit(main())
