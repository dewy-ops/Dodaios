from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .indicators import simple_moving_average, wilder_rsi
from .models import BacktestConfig, Bar, Signal


@dataclass(frozen=True, slots=True)
class OversoldUptrendStrategy:
    """Frozen H1 signal policy; it produces signals but never places orders."""

    config: BacktestConfig = BacktestConfig()

    def signals(self, bars: list[Bar]) -> list[Signal]:
        closes = [bar.close for bar in bars]
        rsi_values = wilder_rsi(closes, self.config.rsi_period)
        averages = simple_moving_average(closes, self.config.sma_period)
        signals: list[Signal] = []
        for index, bar in enumerate(bars):
            if index + 1 < self.config.minimum_history:
                continue
            rsi_value, average = rsi_values[index], averages[index]
            if rsi_value is None or average is None or bar.close < self.config.minimum_close:
                continue
            start = index - self.config.liquidity_lookback
            if start < 0:
                continue
            past_dollar_volume = [
                previous.close * previous.volume for previous in bars[start:index]
            ]
            if median(past_dollar_volume) < self.config.minimum_median_dollar_volume:
                continue
            if bar.close > average and rsi_value < self.config.rsi_threshold:
                signals.append(Signal(bar.security_id, bar.session, index, rsi_value, average))
        return signals
