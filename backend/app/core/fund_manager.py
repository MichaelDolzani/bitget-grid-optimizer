import json
import logging
import time
from ..models import Bot, Event

log = logging.getLogger(__name__)


def run_fund_check(bot_id: int, db_factory):
    from .bitget_client import BitgetClient, BitgetAPIError
    from .indicators import compute_indicators, Candle
    from .optimizer import BotConfig, BotState, decide
    from cryptography.fernet import Fernet
    from ..config import settings
    import traceback

    db = db_factory()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id).first()
        if not bot or not bot.active:
            return

        cfg_dict = json.loads(bot.config_json or "{}")
        if not cfg_dict.get("fund_manager_enabled", True):
            log.debug(f"Bot {bot.id}: fund manager disabled in config")
            return
        min_add = cfg_dict.get("min_add_funds_usdt", settings.min_add_funds_usdt)
        reserve_pct = cfg_dict.get("reserve_pct", settings.reserve_pct) / 100
        fund_check_hours = cfg_dict.get("fund_check_interval_hours", settings.fund_check_interval_hours)

        # Check last reinvestment time (stored in event log -- last FUNDS_ADDED event)
        last_fund_event = (
            db.query(Event)
            .filter(Event.bot_id == bot.id, Event.event_type == "FUNDS_ADDED")
            .order_by(Event.created_at.desc())
            .first()
        )
        if last_fund_event:
            elapsed_hours = (time.time() - last_fund_event.created_at.timestamp()) / 3600
            if elapsed_hours < 24:
                log.debug(f"Bot {bot.id}: fund check skip -- last reinvest {elapsed_hours:.1f}h ago")
                return

        fernet = Fernet(settings.fernet_key.encode())
        api_key = fernet.decrypt(bot.api_key_enc.encode()).decode()
        api_secret = fernet.decrypt(bot.api_secret_enc.encode()).decode()
        passphrase = fernet.decrypt(bot.passphrase_enc.encode()).decode()

        client = BitgetClient(api_key, api_secret, passphrase)
        usdt_free = client.get_spot_balance("USDT")

        # DB is the single source of truth — bot-detail API is not available.
        current_invest = bot.current_invest_amount
        grid_num = bot.current_grid_num
        usdt_per_cell = current_invest / max(grid_num, 1)

        usdt_reserve = usdt_free * reserve_pct
        usdt_usable = usdt_free - usdt_reserve

        if usdt_usable < max(min_add, 2 * usdt_per_cell):
            log.debug(
                f"Bot {bot.id}: insufficient free USDT "
                f"({usdt_free:.2f} available, need {2 * usdt_per_cell:.2f})"
            )
            return

        new_invest = current_invest + usdt_usable
        client.modify_grid_bot(
            bot.bitget_bot_id, bot.symbol,
            bot.current_lower_price, bot.current_upper_price,
            grid_num, new_invest, json.loads(bot.config_json or "{}").get("grid_type", "geometric"),
        )

        bot.current_invest_amount = new_invest
        db.add(Event(
            bot_id=bot.id,
            event_type="FUNDS_ADDED",
            before_json=json.dumps({"invest_amount": current_invest, "usdt_free": usdt_free}),
            after_json=json.dumps({"invest_amount": new_invest, "usdt_added": usdt_usable}),
        ))
        db.commit()
        log.info(f"Bot {bot.id}: added {usdt_usable:.2f} USDT. New invest: {new_invest:.2f}")

        _notify_funds(bot, cfg_dict, usdt_usable, new_invest)

    except Exception as e:
        import traceback
        log.error(f"Bot {bot_id} fund check error: {traceback.format_exc()}")
        db.rollback()
    finally:
        db.close()


def _notify_funds(bot, cfg_dict, usdt_added, new_invest):
    webhook = cfg_dict.get("gchat_webhook_url", "")
    if not webhook or not cfg_dict.get("notify_funds_added", True):
        return
    try:
        from ..notifications.gchat import send as gchat_send
        msg = f"[FONDI] {bot.symbol}: +{usdt_added:.2f} USDT aggiunti. Capitale totale: {new_invest:,.2f} USDT"
        gchat_send(webhook, msg)
    except Exception:
        pass
