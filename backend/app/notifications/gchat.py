import requests
import logging

log = logging.getLogger(__name__)

def send(webhook_url: str, text: str) -> bool:
    """Send a message to a Google Chat webhook. Returns True on success."""
    if not webhook_url:
        return False
    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=5)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"GChat notification failed: {e}")
        return False

def send_daily_summary(webhook_url: str, symbol: str, pnl_24h: float, pnl_pct: float,
                       shifts: int, funds_added: int, grid_num: int) -> bool:
    msg = (f"📊 {symbol} | PnL 24h: {'+' if pnl_24h >= 0 else ''}{pnl_24h:.2f} USDT "
           f"({'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%) | "
           f"Shift: {shifts} | Fondi aggiunti: {funds_added} | Grid attive: {grid_num}")
    return send(webhook_url, msg)
