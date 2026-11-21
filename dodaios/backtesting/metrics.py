from __future__ import annotations

from math import sqrt
from statistics import mean, stdev

from .models import Trade


def win_rate(trades: list[Trade]) -> float:
    return sum(trade.outcome == "win" for trade in trades) / len(trades) if trades else 0.0


def maximum_drawdown(returns: list[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def sharpe_ratio(returns: list[float], annualization_sessions: int = 252) -> float:
    """Annualized zero-risk-rate Sharpe over deterministic portfolio returns."""
    if len(returns) < 2 or stdev(returns) == 0.0:
        return 0.0
    return sqrt(annualization_sessions) * mean(returns) / stdev(returns)
