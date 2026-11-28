from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isclose, isfinite
from statistics import mean
from typing import Protocol

from .metrics import maximum_drawdown, sharpe_ratio, win_rate
from .models import BacktestConfig, Bar, Signal, Trade


class BacktestStrategy(Protocol):
    """Minimal strategy contract consumed by the historical engine."""

    @property
    def config(self) -> BacktestConfig: ...

    def signals(self, bars: list[Bar]) -> list[Signal]: ...


@dataclass(frozen=True, slots=True)
class BacktestResult:
    trades: tuple[Trade, ...]
    daily_returns: tuple[float, ...]
    wins: int
    losses: int
    ties: int
    win_rate: float
    maximum_drawdown: float
    sharpe_ratio: float

    def as_dict(self) -> dict[str, object]:
        return {
            "trades": len(self.trades),
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "win_rate": self.win_rate,
            "maximum_drawdown": self.maximum_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
        }


class BacktestEngine:
    """Pure historical calculator with no execution-layer dependency."""

    def __init__(self, strategy: BacktestStrategy) -> None:
        self.strategy = strategy

    @property
    def config(self) -> BacktestConfig:
        return self.strategy.config

    def run(self, bars: list[Bar]) -> BacktestResult:
        grouped = self._validate_and_group(bars)
        candidates = defaultdict(list)
        for security_bars in grouped.values():
            for signal in self.strategy.signals(security_bars):
                candidates[signal.session].append(signal)

        trades: list[Trade] = []
        position_until: dict[str, int] = {}
        for session in sorted(candidates):
            ranked = sorted(candidates[session], key=lambda item: (item.rsi, item.security_id))
            eligible = [
                signal
                for signal in ranked
                if signal.bar_index > position_until.get(signal.security_id, -1)
            ]
            for signal in eligible[: self.config.daily_signal_cap]:
                security_bars = grouped[signal.security_id]
                entry_index = signal.bar_index + 1
                exit_index = signal.bar_index + self.config.holding_sessions
                if exit_index >= len(security_bars):
                    continue
                entry, exit_bar = security_bars[entry_index], security_bars[exit_index]
                return_fraction = exit_bar.close / entry.open - 1.0
                hurdle_price = entry.open * (1.0 + self.config.outcome_hurdle)
                if isclose(exit_bar.close, hurdle_price, rel_tol=1e-12, abs_tol=1e-12):
                    outcome = "tie"
                elif exit_bar.close > hurdle_price:
                    outcome = "win"
                else:
                    outcome = "loss"
                trades.append(
                    Trade(
                        signal.security_id,
                        signal.session,
                        entry.session,
                        exit_bar.session,
                        entry.open,
                        exit_bar.close,
                        return_fraction,
                        outcome,
                        signal.rsi,
                    )
                )
                position_until[signal.security_id] = exit_index

        # Equal-weight each exit day's completed trades; uninvested days are omitted.
        by_exit = defaultdict(list)
        for trade in trades:
            by_exit[trade.exit_session].append(trade.return_fraction)
        daily_returns = [mean(by_exit[session]) for session in sorted(by_exit)]
        wins = sum(trade.outcome == "win" for trade in trades)
        losses = sum(trade.outcome == "loss" for trade in trades)
        ties = sum(trade.outcome == "tie" for trade in trades)
        return BacktestResult(
            tuple(trades),
            tuple(daily_returns),
            wins,
            losses,
            ties,
            win_rate(trades),
            maximum_drawdown(daily_returns),
            sharpe_ratio(daily_returns, self.config.annualization_sessions),
        )

    @staticmethod
    def _validate_and_group(bars: list[Bar]) -> dict[str, list[Bar]]:
        grouped: dict[str, list[Bar]] = defaultdict(list)
        seen = set()
        for bar in bars:
            key = (bar.security_id, bar.session)
            if key in seen:
                raise ValueError(f"duplicate bar: {bar.security_id} {bar.session}")
            seen.add(key)
            values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if not all(isfinite(value) for value in values):
                raise ValueError(f"non-finite price or volume: {bar.security_id} {bar.session}")
            if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
                raise ValueError(f"invalid price or volume: {bar.security_id} {bar.session}")
            if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
                raise ValueError(f"inconsistent OHLC: {bar.security_id} {bar.session}")
            grouped[bar.security_id].append(bar)
        for security_id, security_bars in grouped.items():
            dates = [bar.session for bar in security_bars]
            if dates != sorted(dates):
                raise ValueError(f"non-monotonic sessions: {security_id}")
        return dict(grouped)
