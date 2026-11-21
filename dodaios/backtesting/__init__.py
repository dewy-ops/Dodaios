"""Offline historical backtesting primitives.

This package deliberately has no dependency on Dodaios execution or broker modules.
"""

from .engine import BacktestEngine, BacktestResult
from .models import BacktestConfig, Bar, Trade
from .strategy import OversoldUptrendStrategy

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "Bar",
    "OversoldUptrendStrategy",
    "Trade",
]
