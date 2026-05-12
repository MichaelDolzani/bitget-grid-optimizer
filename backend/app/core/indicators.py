import math
from typing import NamedTuple


class Candle(NamedTuple):
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class IndicatorResult(NamedTuple):
    atr14: float
    sigma_20d: float
    bb_width: float
    kc_width: float
    ttm_squeeze: bool
    price: float


def compute_indicators(candles: list[Candle]) -> IndicatorResult:
    if len(candles) < 35:
        raise ValueError(f"Need at least 35 candles, got {len(candles)}")

    closes = [c.close for c in candles]
    price = closes[-1]

    true_ranges = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_close = candles[i - 1].close
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        true_ranges.append(tr)

    # Seed ATR with simple average of first 14 TRs, then Wilder EMA forward
    alpha = 1.0 / 14
    atr = sum(true_ranges[:14]) / 14
    for tr in true_ranges[14:]:
        atr = alpha * tr + (1 - alpha) * atr

    # σ_20d over the last 21 closes (20 log returns)
    tail = closes[-21:]
    log_returns = [math.log(tail[i] / tail[i - 1]) for i in range(1, 21)]
    mean = sum(log_returns) / 20
    sigma_20d = math.sqrt(sum((r - mean) ** 2 for r in log_returns) / 20)

    bb_width = 4 * sigma_20d * price
    kc_width = 4 * atr
    ttm_squeeze = bb_width < kc_width

    return IndicatorResult(
        atr14=atr,
        sigma_20d=sigma_20d,
        bb_width=bb_width,
        kc_width=kc_width,
        ttm_squeeze=ttm_squeeze,
        price=price,
    )
