import json
import logging
import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="UTC")


def start(db_factory):
    """db_factory is a callable that returns a DB session"""
    _scheduler.start()
    _reload_bot_jobs(db_factory)
    log.info("Scheduler started")


def stop():
    _scheduler.shutdown(wait=False)


def reload_jobs(db_factory):
    _reload_bot_jobs(db_factory)


def _reload_bot_jobs(db_factory):
    from ..models import Bot
    db = db_factory()
    try:
        bots = db.query(Bot).filter(Bot.active == True).all()
        active_ids = {f"optimize_{b.id}" for b in bots} | {f"fund_{b.id}" for b in bots}

        for job in _scheduler.get_jobs():
            if job.id not in active_ids:
                _scheduler.remove_job(job.id)

        for bot in bots:
            cfg = json.loads(bot.config_json or "{}")
            interval = cfg.get("check_interval_minutes", 30)
            fund_hours = cfg.get("fund_check_interval_hours", 6)

            optimize_id = f"optimize_{bot.id}"
            if not _scheduler.get_job(optimize_id):
                _scheduler.add_job(
                    _run_optimize,
                    trigger=IntervalTrigger(minutes=interval),
                    id=optimize_id,
                    args=[bot.id, db_factory],
                    next_run_time=datetime.utcnow(),
                )

            fund_id = f"fund_{bot.id}"
            if not _scheduler.get_job(fund_id):
                _scheduler.add_job(
                    _run_fund_check,
                    trigger=IntervalTrigger(hours=fund_hours),
                    id=fund_id,
                    args=[bot.id, db_factory],
                )
    finally:
        db.close()


def _run_fund_check(bot_id: int, db_factory):
    from .fund_manager import run_fund_check
    run_fund_check(bot_id, db_factory)


def _run_optimize(bot_id: int, db_factory):
    from ..models import Bot, Event, PnlSnapshot
    from .bitget_client import BitgetClient, BitgetAPIError
    from .indicators import compute_indicators, Candle
    from .optimizer import decide, BotConfig, BotState
    from cryptography.fernet import Fernet
    from ..config import settings
    import traceback

    bot = None
    cfg_dict: dict = {}
    db = db_factory()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if not bot or not bot.active:
            return

        fernet = Fernet(settings.fernet_key.encode())
        api_key = fernet.decrypt(bot.api_key_enc.encode()).decode()
        api_secret = fernet.decrypt(bot.api_secret_enc.encode()).decode()
        passphrase = fernet.decrypt(bot.passphrase_enc.encode()).decode()

        client = BitgetClient(api_key, api_secret, passphrase)
        cfg_dict = json.loads(bot.config_json or "{}")
        config = BotConfig(
            bot_id=bot.bitget_bot_id,
            symbol=bot.symbol,
            grid_type=cfg_dict.get("grid_type", "geometric"),
            atr_multiplier=cfg_dict.get("atr_multiplier", 2.5),
            sigma_multiplier=cfg_dict.get("sigma_multiplier", 1.5),
            step_target_pct=cfg_dict.get("step_target_pct", 0.8),
            max_grid_count=cfg_dict.get("max_grid_count", 150),
            shift_threshold_pct=cfg_dict.get("shift_threshold_pct", 5.0),
            cooldown_minutes=cfg_dict.get("cooldown_minutes", 60),
            ttm_squeeze_enabled=cfg_dict.get("ttm_squeeze_enabled", True),
            volatility_spike_multiplier=cfg_dict.get("volatility_spike_multiplier", 1.5),
            volatility_spike_range_expand=cfg_dict.get("volatility_spike_range_expand", 1.20),
        )

        candles_raw = client.get_candles(bot.symbol, "1h", limit=50)
        candles = [Candle(**c) for c in candles_raw]
        indicators = compute_indicators(candles)

        # DB is the single source of truth for bot state — bot-detail API is not available.
        state = BotState(
            lower_price=bot.current_lower_price,
            upper_price=bot.current_upper_price,
            grid_num=bot.current_grid_num,
            invest_amount=bot.current_invest_amount,
            last_shift_ts=bot.last_shift_ts,
        )

        decision = decide(indicators, state, config, atr14_avg20=0.0)

        # PnL snapshot — committed independently so it's never lost on shift errors.
        db.add(PnlSnapshot(
            bot_id=bot.id,
            total_pnl=0.0,
            grid_profit=0.0,
            floating_pnl=0.0,
            price=indicators.price,
            invest_amount=state.invest_amount,
        ))
        db.commit()

        shift_result = None
        if decision.should_shift:
            before = {"lower": state.lower_price, "upper": state.upper_price, "grid_num": state.grid_num}
            after = {"lower": decision.new_lower, "upper": decision.new_upper, "grid_num": decision.new_grid_num}
            try:
                client.modify_grid_bot(
                    bot.bitget_bot_id, bot.symbol,
                    decision.new_lower, decision.new_upper, decision.new_grid_num,
                    state.invest_amount, config.grid_type,
                )
                db.add(Event(bot_id=bot.id, event_type="SHIFT_TRIGGERED",
                             before_json=json.dumps(before), after_json=json.dumps(after)))
                bot.last_shift_ts = time.time()
                bot.current_lower_price = decision.new_lower
                bot.current_upper_price = decision.new_upper
                bot.current_grid_num = decision.new_grid_num
                shift_result = "triggered"
                log.info(f"Bot {bot.id} ({bot.symbol}): shift triggered {before} -> {after}")
            except BitgetAPIError as api_err:
                # modify-grid not available for spot bots — record recommendation only.
                db.add(Event(bot_id=bot.id, event_type="SHIFT_RECOMMENDED",
                             before_json=json.dumps(before), after_json=json.dumps(after)))
                shift_result = "recommended"
                log.warning(f"Bot {bot.id}: shift recommended but API unavailable: {api_err}")
            db.commit()
        elif decision.reason == "TTM_SQUEEZE":
            db.add(Event(bot_id=bot.id, event_type="TTM_SQUEEZE_SKIP",
                         before_json="{}", after_json="{}"))
            db.commit()
            log.info(f"Bot {bot.id}: TTM squeeze -- skip")
        else:
            log.info(f"Bot {bot.id}: no shift ({decision.reason})")

        # Send notification
        _notify(bot, decision, cfg_dict, shift_result=shift_result)

    except Exception as e:
        log.error(f"Bot {bot_id} optimize error: {traceback.format_exc()}")
        db.rollback()
        db.add(Event(bot_id=bot_id, event_type="ERROR",
                     before_json="{}", after_json=json.dumps({"error": str(e)})))
        db.commit()
        if bot is not None:
            _notify_error(bot, cfg_dict, str(e))
    finally:
        db.close()


def _notify(bot, decision, cfg_dict, shift_result=None):
    webhook = cfg_dict.get("gchat_webhook_url", "")
    if not webhook:
        return
    try:
        from ..notifications.gchat import send as gchat_send
        if decision.should_shift:
            if not cfg_dict.get("notify_shift_range", True):
                return
            label = "[SHIFT ESEGUITO]" if shift_result == "triggered" else "[SHIFT RACCOMANDATO]"
            msg = (f"{label} {bot.symbol}: "
                   f"[{decision.new_lower:,.0f} → {decision.new_upper:,.0f}] "
                   f"(era [{decision.current_lower:,.0f} → {decision.current_upper:,.0f}]). "
                   f"Grid: {decision.new_grid_num} (era {decision.current_grid_num})")
        elif decision.reason == "TTM_SQUEEZE":
            if not cfg_dict.get("notify_ttm_squeeze_skip", True):
                return
            msg = f"[PAUSA] {bot.symbol}: shift saltato — mercato in compressione. Monitoring attivo."
        else:
            return
        gchat_send(webhook, msg)
    except Exception:
        pass


def _notify_error(bot, cfg_dict: dict, error_msg: str):
    webhook = cfg_dict.get("gchat_webhook_url", "")
    if not webhook or not cfg_dict.get("notify_errors", True):
        return
    try:
        from ..notifications.gchat import send as gchat_send
        gchat_send(webhook, f"[ERRORE] {bot.symbol} (bot {bot.id}): {error_msg}")
    except Exception:
        pass
