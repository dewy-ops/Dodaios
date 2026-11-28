# Stock Trading Framework S3 — offline backtesting implementation

## Result

The historical backtesting engine is implemented in `dodaios.backtesting`. It is architecturally
separate from order execution: its public inputs are immutable OHLCV bars and strategy settings,
and its outputs are immutable trades, daily diagnostic returns, and metrics. It imports no broker,
account, credential, order, paper-trading, or live-trading modules.

The selected H1 candidate from S2 is encoded with its frozen defaults: RSI(2) below 10, close above
SMA(200), at least 252 observations, a USD 5 close floor, lagged 20-session median dollar volume of
USD 20 million, a daily cap of 10 ranked by RSI then permanent security ID, next-session-open entry,
third-subsequent-session-close exit, open-position suppression, and a strict 20-basis-point win
hurdle.

## Verification result

No approved versioned historical market snapshot exists in the repository. Consequently, no real
market result and no acceptance or falsification decision for H1 can be reported. The executable
synthetic fixture produced:

| Metric | Synthetic verification value |
| --- | ---: |
| Trades | 1 |
| Wins / losses / ties | 1 / 0 / 0 |
| Win rate | 100% |
| Maximum drawdown | 0% |
| Annualized Sharpe ratio | 0.0 |

Sharpe is zero because one completed-trade return does not provide enough observations to estimate
return volatility. These values validate mechanics only; they are not historical performance,
expected performance, or an authorization to paper or live trade.

The diagnostic portfolio convention required to make drawdown and Sharpe reproducible is equal
weight across trades sharing an exit session, with exit-session returns compounded, a zero
risk-free rate, and 252-session annualization. This convention is deliberately isolated from the
H1 signal and trade-event rules.

## Reproduction

```powershell
python scripts/backtest_stock_h1.py --demo
python -m pytest tests/unit/backtesting/test_engine.py -q
```

For an approved offline snapshot, use:

```powershell
python scripts/backtest_stock_h1.py --input snapshot.csv --output result.json
```

The engine validates duplicate keys, chronological sessions per security, positive prices,
non-negative volume, and OHLC consistency. RSI, MACD, and fixed-bin volume-profile calculations
are exposed as independent indicator functions and covered by unit tests.

## Boundary

This stage performed offline calculations only. It did not access a broker or account, read trading
credentials, submit an order, simulate an order against an account, download market data, or run a
paper/live execution path. The synthetic JSON receipt is stored at
`data-dev/audits/STOCK_TRADING_FRAMEWORK_S3_SYNTHETIC_RESULTS.json`.

## Continuation review (2026-11-27)

The implementation was re-run from a clean checkout using the commands above. The documented
script entry point now resolves the repository package when invoked directly. Seven focused tests
pass, covering RSI, MACD, volume profile, a known drawdown and Sharpe value, next-open/third-close
timing, overlap suppression, exact-hurdle tie handling, daily-cap ordering, and corrupt/non-finite
input rejection.

The review also corrected the selection sequence so a signal from a symbol with an already-open
position is removed before the daily cap is applied. This prevents an ignored repeat signal from
displacing another eligible security. The engine now consumes a structural `BacktestStrategy`
protocol rather than the H1 class directly, preserving separation between the reusable historical
calculator, signal policy, and any execution layer.

The synthetic result is unchanged: one trade, one win, no losses or ties, 100% win rate, 0%
maximum drawdown, and a 0.0 Sharpe ratio because one return cannot estimate volatility. This is a
mechanical verification result only. With no approved historical snapshot in the repository, H1
still has no empirical acceptance or falsification result.
