# Offline stock backtesting engine

The engine in `dodaios.backtesting` is a pure historical calculator. It imports no broker,
account, order, or execution code. `OversoldUptrendStrategy` implements the frozen H1 candidate:
RSI(2) below 10 while the adjusted close is above SMA(200), followed by entry at the next
session's open and exit at the third subsequent session's close.

Run a deterministic, non-market verification:

```powershell
python scripts/backtest_stock_h1.py --demo
```

Run an offline CSV snapshot:

```powershell
python scripts/backtest_stock_h1.py --input snapshot.csv --output result.json
```

The CSV columns are `security_id,session,open,high,low,close,volume`; sessions must be ISO dates
and rows must be chronological within each security. Duplicate keys, non-positive prices,
negative volume, invalid OHLC relationships, and non-monotonic histories fail closed.

Win rate uses the research protocol's strict 20-basis-point hurdle. Maximum drawdown and Sharpe
use a separately stated diagnostic portfolio convention: completed trades are equal-weighted by
exit session, with a zero risk-free rate and 252-session annualization. This makes those metrics
reproducible without coupling the engine to an order executor. They are diagnostics, not a claim
of attainable portfolio performance.

The repository contains no approved, point-in-time historical snapshot. Therefore the demo output
verifies behavior only and cannot accept or falsify H1. A market result requires the security
master, session calendar, corporate-action ledger, and versioned hashes specified by the S2
research document.
