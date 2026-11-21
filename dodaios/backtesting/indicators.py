from __future__ import annotations

from dataclasses import dataclass
from math import fsum


def simple_moving_average(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    output: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            output[index] = running / period
    return output


def wilder_rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Return Wilder RSI seeded with the arithmetic mean of the first deltas."""
    if period <= 0:
        raise ValueError("period must be positive")
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return output
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    average_gain = fsum(max(delta, 0.0) for delta in deltas[:period]) / period
    average_loss = fsum(max(-delta, 0.0) for delta in deltas[:period]) / period

    def value() -> float:
        if average_loss == 0.0:
            return 50.0 if average_gain == 0.0 else 100.0
        if average_gain == 0.0:
            return 0.0
        ratio = average_gain / average_loss
        return 100.0 - 100.0 / (1.0 + ratio)

    output[period] = value()
    for delta_index in range(period, len(deltas)):
        gain = max(deltas[delta_index], 0.0)
        loss = max(-deltas[delta_index], 0.0)
        average_gain = ((period - 1) * average_gain + gain) / period
        average_loss = ((period - 1) * average_loss + loss) / period
        output[delta_index + 1] = value()
    return output


def exponential_moving_average(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    output: list[float | None] = [None] * len(values)
    if len(values) < period:
        return output
    current = fsum(values[:period]) / period
    output[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current = alpha * values[index] + (1.0 - alpha) * current
        output[index] = current
    return output


@dataclass(frozen=True, slots=True)
class MacdPoint:
    macd: float | None
    signal: float | None
    histogram: float | None


def macd(
    values: list[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
) -> list[MacdPoint]:
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    fast = exponential_moving_average(values, fast_period)
    slow = exponential_moving_average(values, slow_period)
    line = [
        fast_value - slow_value
        for fast_value, slow_value in zip(fast, slow, strict=True)
        if fast_value is not None and slow_value is not None
    ]
    signal_values = exponential_moving_average(line, signal_period)
    first_line_index = slow_period - 1
    output = [MacdPoint(None, None, None) for _ in values]
    for offset, macd_value in enumerate(line):
        signal_value = signal_values[offset]
        output[first_line_index + offset] = MacdPoint(
            macd_value,
            signal_value,
            None if signal_value is None else macd_value - signal_value,
        )
    return output


@dataclass(frozen=True, slots=True)
class VolumeProfileBin:
    low: float
    high: float
    volume: float


def volume_profile(
    prices: list[float], volumes: list[float], bins: int = 10
) -> list[VolumeProfileBin]:
    if len(prices) != len(volumes) or not prices:
        raise ValueError("prices and volumes must have the same non-zero length")
    if bins <= 0:
        raise ValueError("bins must be positive")
    low, high = min(prices), max(prices)
    width = (high - low) / bins if high != low else 1.0
    totals = [0.0] * bins
    for price, volume in zip(prices, volumes, strict=True):
        index = min(int((price - low) / width), bins - 1)
        totals[index] += volume
    return [
        VolumeProfileBin(low + index * width, low + (index + 1) * width, total)
        for index, total in enumerate(totals)
    ]
