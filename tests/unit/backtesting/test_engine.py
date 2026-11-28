from __future__ import annotations

from datetime import date, timedelta

import pytest

from dodaios.backtesting import BacktestConfig, BacktestEngine, Bar, OversoldUptrendStrategy
from dodaios.backtesting.indicators import macd, volume_profile, wilder_rsi
from dodaios.backtesting.metrics import maximum_drawdown, sharpe_ratio
from dodaios.backtesting.models import Signal


def bars_from_closes(closes: list[float]) -> list[Bar]:
    return [
        Bar(
            "SEC1",
            date(2025, 1, 1) + timedelta(days=index),
            closes[index - 1] if index else close,
            max(closes[index - 1] if index else close, close) + 1,
            min(closes[index - 1] if index else close, close) - 1,
            close,
            1_000_000,
        )
        for index, close in enumerate(closes)
    ]


def test_indicators_cover_rsi_macd_and_volume_profile() -> None:
    assert wilder_rsi([10, 9, 8], 2)[-1] == 0.0
    points = macd([float(value) for value in range(1, 50)])
    assert points[-1].macd is not None
    assert points[-1].signal is not None
    profile = volume_profile([10, 11, 12, 13], [100, 200, 300, 400], bins=2)
    assert sum(item.volume for item in profile) == 1000


def test_core_metrics_have_known_values() -> None:
    returns = [0.1, -0.2, 0.05]

    assert maximum_drawdown(returns) == pytest.approx(-0.2)
    assert sharpe_ratio(returns, annualization_sessions=1) == pytest.approx(-0.1036951695)


def test_h1_uses_next_open_third_session_close_and_metrics() -> None:
    closes = [100 + index for index in range(35)]
    closes[25:] = [124, 123, 122, 121, 120, 123, 125, 126, 127, 128]
    config = BacktestConfig(
        minimum_history=20,
        sma_period=20,
        liquidity_lookback=3,
        minimum_median_dollar_volume=0,
        holding_sessions=3,
    )
    result = BacktestEngine(OversoldUptrendStrategy(config)).run(bars_from_closes(closes))
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 121
    assert result.trades[0].exit_price == 125
    assert result.wins == 1
    assert result.win_rate == 1.0
    assert result.maximum_drawdown == 0.0


def test_open_position_suppresses_overlapping_signal() -> None:
    closes = [100 + index for index in range(35)]
    closes[25:] = [124, 123, 122, 121, 120, 123, 125, 126, 127, 128]
    config = BacktestConfig(
        minimum_history=20,
        sma_period=20,
        liquidity_lookback=2,
        minimum_median_dollar_volume=0,
        holding_sessions=3,
    )
    result = BacktestEngine(OversoldUptrendStrategy(config)).run(bars_from_closes(closes))
    signal_dates = [trade.signal_session for trade in result.trades]
    assert len(signal_dates) == len(set(signal_dates)) == 1


def test_exact_hurdle_is_a_tie_despite_float_rounding() -> None:
    closes = [100 + index for index in range(35)]
    closes[25:] = [124, 123, 122, 121, 120, 121, 122, 123, 124, 125]
    bars = bars_from_closes(closes)
    config = BacktestConfig(
        minimum_history=20,
        sma_period=20,
        liquidity_lookback=3,
        minimum_median_dollar_volume=0,
        holding_sessions=3,
    )
    signal_index = 28
    entry_index = signal_index + 1
    exit_index = signal_index + config.holding_sessions
    entry = bars[entry_index]
    tied_close = entry.open * (1 + config.outcome_hurdle)
    bars[exit_index] = Bar(
        bars[exit_index].security_id,
        bars[exit_index].session,
        bars[exit_index].open,
        max(bars[exit_index].open, tied_close),
        min(bars[exit_index].open, tied_close),
        tied_close,
        bars[exit_index].volume,
    )

    result = BacktestEngine(OversoldUptrendStrategy(config)).run(bars)

    assert result.ties == 1
    assert result.wins == 0
    assert result.win_rate == 0.0


def test_open_position_signal_does_not_consume_daily_cap() -> None:
    config = BacktestConfig(
        minimum_history=1,
        sma_period=1,
        liquidity_lookback=1,
        minimum_median_dollar_volume=0,
        holding_sessions=3,
        daily_signal_cap=1,
    )
    sessions = [date(2025, 1, 1) + timedelta(days=index) for index in range(6)]

    class FixedStrategy:
        def __init__(self) -> None:
            self.config = config

        def signals(self, bars: list[Bar]) -> list[Signal]:
            if bars[0].security_id == "A":
                return [
                    Signal("A", sessions[0], 0, 1.0, 1.0),
                    Signal("A", sessions[1], 1, 1.0, 1.0),
                ]
            return [Signal("B", sessions[1], 1, 2.0, 1.0)]

    bars = []
    for security_id in ("A", "B"):
        bars.extend(Bar(security_id, session, 100, 101, 99, 101, 1_000) for session in sessions)

    result = BacktestEngine(FixedStrategy()).run(bars)

    assert [(trade.security_id, trade.signal_session) for trade in result.trades] == [
        ("A", sessions[0]),
        ("B", sessions[1]),
    ]


def test_rejects_duplicate_and_corrupt_bars() -> None:
    valid = bars_from_closes([10, 11])
    engine = BacktestEngine(OversoldUptrendStrategy())
    with pytest.raises(ValueError, match="duplicate"):
        engine.run([valid[0], valid[0]])
    corrupt = Bar("SEC1", date(2025, 1, 1), 10, 9, 8, 10, 1)
    with pytest.raises(ValueError, match="inconsistent OHLC"):
        engine.run([corrupt])
    non_finite = Bar("SEC1", date(2025, 1, 1), 10, 10, 10, float("nan"), 1)
    with pytest.raises(ValueError, match="non-finite"):
        engine.run([non_finite])
