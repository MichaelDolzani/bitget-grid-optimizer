"""
Bitget Grid Bot Optimizer — Proof of Concept CLI

Usage:
    python -m app.poc --dry-run   # compute and print, no live API calls
    python -m app.poc             # run against real Bitget API
"""

import argparse
import os
import sys
from typing import NamedTuple

from dotenv import load_dotenv

from app.core.bitget_client import BitgetClient
from app.core.indicators import Candle, compute_indicators
from app.core.optimizer import BotConfig, BotState, decide


# ─── helpers ────────────────────────────────────────────────────────────────

def _fmt_price(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def _pct_delta(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return abs(new - old) / old * 100.0


def _print_separator(char: str = "═", width: int = 39) -> None:
    print(char * width)


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bitget Grid Bot Optimizer — PoC",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print decision without executing any live API call",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    # 1. Load .env
    load_dotenv()

    api_key = os.getenv("BITGET_API_KEY", "")
    api_secret = os.getenv("BITGET_API_SECRET", "")
    passphrase = os.getenv("BITGET_PASSPHRASE", "")
    bot_id = os.getenv("BOT_ID", "")
    symbol = os.getenv("SYMBOL", "BTCUSDT")

    if not all([api_key, api_secret, passphrase, bot_id]):
        print(
            "ERROR: Missing required env vars. "
            "Set BITGET_API_KEY, BITGET_API_SECRET, BITGET_PASSPHRASE, BOT_ID.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        # 2. Build client
        client = BitgetClient(api_key, api_secret, passphrase)

        # 3. Fetch 50 candles 1H
        raw_candles = client.get_candles(symbol, granularity="1h", limit=50)

        # 4. Convert to Candle NamedTuples and compute indicators
        candles = [
            Candle(
                ts=c["ts"],
                open=c["open"],
                high=c["high"],
                low=c["low"],
                close=c["close"],
                volume=c["volume"],
            )
            for c in raw_candles
        ]
        indicators = compute_indicators(candles)

        # 5. Fetch bot detail and build BotState.
        # Falls back to env vars if the bot-detail API is unavailable (e.g. Unified Account).
        bot_detail = {}
        try:
            bot_detail = client.get_bot_detail(bot_id)
        except Exception as e:
            print(f"[WARN] bot-detail API non disponibile ({e}); uso valori da .env", file=sys.stderr)

        lower_price = float(bot_detail.get("lowerPrice") or os.getenv("BOT_LOWER_PRICE") or 0)
        upper_price = float(bot_detail.get("upperPrice") or os.getenv("BOT_UPPER_PRICE") or 0)
        grid_num    = int(float(bot_detail.get("gridNum")  or os.getenv("BOT_GRID_NUM")    or 0))
        invest_amt  = float(bot_detail.get("investAmount") or os.getenv("BOT_INVEST_AMOUNT") or 0)

        state = BotState(
            lower_price=lower_price,
            upper_price=upper_price,
            grid_num=grid_num,
            invest_amount=invest_amt,
            last_shift_ts=0.0,
        )
        grid_type = (
            bot_detail.get("gridType")
            or os.getenv("GRID_TYPE", "geometric")
        )

        # 6. Build BotConfig with defaults
        config = BotConfig(
            bot_id=bot_id,
            symbol=symbol,
            grid_type=grid_type,
            shift_threshold_pct=float(os.getenv("SHIFT_THRESHOLD_PCT", "5.0")),
            atr_multiplier=float(os.getenv("ATR_MULTIPLIER", "2.5")),
            sigma_multiplier=float(os.getenv("SIGMA_MULTIPLIER", "1.5")),
            step_target_pct=float(os.getenv("STEP_TARGET_PCT", "0.8")),
            max_grid_count=int(os.getenv("MAX_GRID_COUNT", "150")),
            cooldown_minutes=int(os.getenv("COOLDOWN_MINUTES", "60")),
            ttm_squeeze_enabled=os.getenv("TTM_SQUEEZE_ENABLED", "true").lower() == "true",
            volatility_spike_multiplier=float(os.getenv("VOLATILITY_SPIKE_MULTIPLIER", "1.5")),
            volatility_spike_range_expand=float(os.getenv("VOLATILITY_SPIKE_RANGE_EXPAND", "1.20")),
        )

        # 7. ATR avg (PoC approximation: pass 0.0 to skip spike check)
        atr14_avg20 = 0.0

        # 8. Decide
        decision = decide(indicators, state, config, atr14_avg20=atr14_avg20)

        # 9. Print summary
        mode_label = "DRY RUN" if dry_run else "LIVE"
        _print_separator("═")
        print(f" BITGET GRID BOT OPTIMIZER — {mode_label}")
        _print_separator("═")
        print(f"Symbol:          {symbol}")
        print(f"Current price:   {_fmt_price(indicators.price)}")
        print()

        print("── Indicatori ──────────────────────────")
        print(f"ATR(14):         {_fmt_price(indicators.atr14)}")
        print(f"σ_20d:           {_fmt_pct(indicators.sigma_20d * 100)}")
        squeeze_label = "SÌ  ✗" if indicators.ttm_squeeze else "NO  ✓"
        print(f"TTM Squeeze:     {squeeze_label}")
        print()

        print("── Range ottimale ──────────────────────")
        print(f"Lower:           {_fmt_price(decision.new_lower)}")
        print(f"Upper:           {_fmt_price(decision.new_upper)}")
        print(f"Grid count:      {decision.new_grid_num}")
        if decision.new_lower > 0 and decision.new_grid_num > 1:
            if grid_type == "arithmetic":
                step_pct = (decision.new_upper - decision.new_lower) / decision.new_grid_num / decision.new_lower * 100.0
            else:
                step_pct = (
                    (decision.new_upper / decision.new_lower) ** (1.0 / decision.new_grid_num) - 1.0
                ) * 100.0
            print(f"Step effettivo:  {_fmt_pct(step_pct)} ({'aritmetico' if grid_type == 'arithmetic' else 'geometrico'})")
        print()

        print("── Bot attuale ─────────────────────────")
        print(f"Lower:           {_fmt_price(state.lower_price)}")
        print(f"Upper:           {_fmt_price(state.upper_price)}")
        print(f"Grid count:      {state.grid_num}")
        print()

        print("── Decisione ───────────────────────────")
        if decision.should_shift:
            delta_upper = _pct_delta(decision.new_upper, state.upper_price)
            delta_lower = _pct_delta(decision.new_lower, state.lower_price)
            print(f"Shift:           SÌ — {decision.reason}")
            print(f"Delta upper:     {_fmt_pct(delta_upper)}")
            print(f"Delta lower:     {_fmt_pct(delta_lower)}")
        else:
            print(f"Shift:           NO — {decision.reason}")
        print()

        if dry_run:
            print("[DRY RUN — nessuna modifica eseguita]")
        else:
            print("Invio modifica a Bitget...")
            response = client.modify_grid_bot(
                bot_id=bot_id,
                symbol=symbol,
                lower_price=decision.new_lower,
                upper_price=decision.new_upper,
                grid_num=decision.new_grid_num,
                invest_amount=state.invest_amount,
                grid_type=config.grid_type,
            )
            print(f"Risposta API: {response}")

        _print_separator("═")

    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
