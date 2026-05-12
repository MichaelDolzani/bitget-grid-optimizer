import math
import time
from dataclasses import dataclass, field

from .indicators import IndicatorResult


@dataclass
class BotConfig:
    bot_id: str
    symbol: str
    grid_type: str = "geometric"
    atr_multiplier: float = 2.5
    sigma_multiplier: float = 1.5
    step_target_pct: float = 0.8
    max_grid_count: int = 150
    shift_threshold_pct: float = 5.0
    cooldown_minutes: int = 60
    ttm_squeeze_enabled: bool = True
    volatility_spike_multiplier: float = 1.5
    volatility_spike_range_expand: float = 1.20


@dataclass
class BotState:
    lower_price: float
    upper_price: float
    grid_num: int
    invest_amount: float
    last_shift_ts: float = 0.0


@dataclass
class OptimizerDecision:
    should_shift: bool
    reason: str
    new_lower: float
    new_upper: float
    new_grid_num: int
    current_lower: float
    current_upper: float
    current_grid_num: int
    atr14: float
    sigma_20d: float
    ttm_squeeze: bool


def decide(
    indicators: IndicatorResult,
    state: BotState,
    config: BotConfig,
    atr14_avg20: float = 0.0,
) -> OptimizerDecision:
    price = indicators.price
    atr14 = indicators.atr14
    sigma_20d = indicators.sigma_20d

    base = OptimizerDecision(
        should_shift=False,
        reason="",
        new_lower=0.0,
        new_upper=0.0,
        new_grid_num=0,
        current_lower=state.lower_price,
        current_upper=state.upper_price,
        current_grid_num=state.grid_num,
        atr14=atr14,
        sigma_20d=sigma_20d,
        ttm_squeeze=indicators.ttm_squeeze,
    )

    half_range = _compute_half_range(config, atr14, sigma_20d, price, atr14_avg20)
    new_lower, new_upper, new_grid_num = _compute_range(config, price, half_range)

    base.new_lower = new_lower
    base.new_upper = new_upper
    base.new_grid_num = new_grid_num

    # Range width misalignment: current range is >2x wider or <0.5x narrower than optimal
    current_width = state.upper_price - state.lower_price
    optimal_width = new_upper - new_lower
    if current_width > 0 and optimal_width > 0:
        width_ratio = current_width / optimal_width
        range_misaligned = width_ratio > 2.0 or width_ratio < 0.5
    else:
        range_misaligned = False

    # TTM Squeeze guardrail: skip only when range is not severely misaligned
    if config.ttm_squeeze_enabled and indicators.ttm_squeeze and not range_misaligned:
        base.reason = "TTM_SQUEEZE"
        return base

    # Near boundary: within 15% of range width from either side
    if current_width > 0:
        near_boundary = (
            price >= state.upper_price - 0.15 * current_width
            or price <= state.lower_price + 0.15 * current_width
        )
    else:
        near_boundary = True

    threshold = config.shift_threshold_pct / 100
    delta_exceeded = (
        abs(new_upper - state.upper_price) / state.upper_price > threshold
        or abs(new_lower - state.lower_price) / state.lower_price > threshold
    )
    cooled_down = (time.time() - state.last_shift_ts) >= config.cooldown_minutes * 60

    if cooled_down and (range_misaligned or (near_boundary and delta_exceeded)):
        base.should_shift = True
        base.reason = "RANGE_MISALIGNED" if range_misaligned else "SHIFT_TRIGGERED"
    elif not cooled_down:
        base.reason = "COOLDOWN_ACTIVE"
    elif not near_boundary and not range_misaligned:
        base.reason = "PRICE_WITHIN_BOUNDS"
    else:
        base.reason = "DELTA_BELOW_THRESHOLD"

    return base


def _compute_half_range(
    config: BotConfig,
    atr14: float,
    sigma_20d: float,
    price: float,
    atr14_avg20: float,
) -> float:
    half_range = max(
        config.atr_multiplier * atr14,
        config.sigma_multiplier * sigma_20d * price,
    )
    if atr14_avg20 > 0 and atr14 > atr14_avg20 * config.volatility_spike_multiplier:
        half_range *= config.volatility_spike_range_expand
    return half_range


def _compute_range(
    config: BotConfig, price: float, half_range: float
) -> tuple[float, float, int]:
    new_upper = price + half_range
    new_lower = price - half_range
    return new_lower, new_upper, _optimal_grid_num(config, half_range, price)


def _optimal_grid_num(config: BotConfig, half_range: float, price: float) -> int:
    """Compute grid count so each cell is step_target_pct % of mid-price.
    Clamped between 2 (minimum viable grid) and max_grid_count (safety cap).
    """
    step_usdt = price * config.step_target_pct / 100
    if step_usdt <= 0:
        return config.max_grid_count
    raw = (2 * half_range) / step_usdt
    return max(2, min(config.max_grid_count, round(raw)))
