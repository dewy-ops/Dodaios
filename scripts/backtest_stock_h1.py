"""Run the offline H1 historical backtest from CSV or a deterministic demo fixture."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

from dodaios.backtesting import BacktestEngine, Bar, OversoldUptrendStrategy


def load_csv(path: Path) -> list[Bar]:
    with path.open(encoding="utf-8", newline="") as stream:
        return [
            Bar(
                row["security_id"],
                date.fromisoformat(row["session"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
            for row in csv.DictReader(stream)
        ]


def demo_bars() -> list[Bar]:
    bars = []
    session = date(2025, 1, 1)
    closes = [100.0 + index * 0.12 for index in range(260)]
    # Three separated oversold shocks, followed by two recoveries and one further decline.
    for index, movement in ((252, -4.0), (253, -3.0), (256, 5.0), (257, 2.0)):
        closes[index] = closes[index - 1] + movement
    while len(bars) < len(closes):
        index = len(bars)
        if session.weekday() >= 5:
            session += timedelta(days=1)
            continue
        close = closes[index]
        open_price = closes[index - 1] if index else close
        bars.append(
            Bar(
                "SYNTHETIC",
                session,
                open_price,
                max(open_price, close) + 1,
                min(open_price, close) - 1,
                close,
                500_000,
            )
        )
        session += timedelta(days=1)
    return bars


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="UTF-8 OHLCV CSV matching the documented schema")
    source.add_argument(
        "--demo", action="store_true", help="run deterministic synthetic verification data"
    )
    parser.add_argument("--output", type=Path, help="optional JSON result path")
    args = parser.parse_args()
    bars = demo_bars() if args.demo else load_csv(args.input)
    result = BacktestEngine(OversoldUptrendStrategy()).run(bars)
    payload = {
        "dataset": "synthetic-verification-only" if args.demo else str(args.input),
        "hypothesis": "H1: RSI(2) < 10 and close > SMA(200)",
        "portfolio_model": "equal-weight completed trades grouped by exit session",
        **result.as_dict(),
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
