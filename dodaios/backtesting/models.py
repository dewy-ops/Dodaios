from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Bar:
    security_id: str
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    minimum_history: int = 252
    sma_period: int = 200
    rsi_period: int = 2
    rsi_threshold: float = 10.0
    minimum_close: float = 5.0
    liquidity_lookback: int = 20
    minimum_median_dollar_volume: float = 20_000_000.0
    holding_sessions: int = 3
    outcome_hurdle: float = 0.002
    daily_signal_cap: int = 10
    annualization_sessions: int = 252


@dataclass(frozen=True, slots=True)
class Signal:
    security_id: str
    session: date
    bar_index: int
    rsi: float
    sma: float


@dataclass(frozen=True, slots=True)
class Trade:
    security_id: str
    signal_session: date
    entry_session: date
    exit_session: date
    entry_price: float
    exit_price: float
    return_fraction: float
    outcome: str
    rsi: float
