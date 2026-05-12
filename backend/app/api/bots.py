"""
Bot CRUD, history, PnL chart data, and connection testing.

Sensitive credentials (api_key, api_secret, passphrase) are encrypted with
Fernet (symmetric AES-128-CBC + HMAC) before being persisted and never returned
to callers.  The Fernet key is taken from settings.fernet_key (base64-urlsafe,
32 bytes before encoding).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..core import scheduler as _scheduler

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..api.auth import get_current_user
from ..config import settings
from ..core.bitget_client import BitgetClient
from ..database import get_db, SessionLocal
from ..models import Bot, Event, PnlSnapshot, User

router = APIRouter()

# ---------------------------------------------------------------------------
# Fernet helper — lazy-initialised so the app can import without a valid key
# ---------------------------------------------------------------------------

_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not settings.fernet_key:
            raise HTTPException(
                status_code=500,
                detail="Encryption key not configured (FERNET_KEY missing)",
            )
        _fernet = Fernet(settings.fernet_key.encode())
    return _fernet


def _encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Credential decryption failed") from exc


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class BotCreate(BaseModel):
    symbol: str
    bitget_bot_id: str
    api_key: str
    api_secret: str
    passphrase: str
    lower_price: float = 0.0
    upper_price: float = 0.0
    grid_num: int = 0
    invest_amount: float = 0.0
    config: dict = {}


class BotUpdate(BaseModel):
    symbol: Optional[str] = None
    bitget_bot_id: Optional[str] = None
    config: Optional[dict] = None
    active: Optional[bool] = None


# ---------------------------------------------------------------------------
# Ownership helper
# ---------------------------------------------------------------------------

def _get_bot_or_403(bot_id: int, user: User, db: Session) -> Bot:
    """
    Return the Bot row if it exists and the requesting user owns it (or is an
    admin).  Raises 404 when the bot doesn't exist, 403 on ownership failure.
    """
    bot = db.query(Bot).filter(Bot.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    if user.role != "admin" and bot.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return bot


# ---------------------------------------------------------------------------
# Safe serialiser — never exposes encrypted credential columns
# ---------------------------------------------------------------------------

def _bot_out(bot: Bot) -> dict[str, Any]:
    return {
        "id": bot.id,
        "symbol": bot.symbol,
        "bitget_bot_id": bot.bitget_bot_id,
        "active": bot.active,
        "config": bot.config_json if isinstance(bot.config_json, dict) else json.loads(bot.config_json or "{}"),
        "last_shift_ts": bot.last_shift_ts,
        "current_lower_price": bot.current_lower_price,
        "current_upper_price": bot.current_upper_price,
        "current_grid_num": bot.current_grid_num,
        "current_invest_amount": bot.current_invest_amount,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", summary="List bots for the current user")
def list_bots(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all bots owned by the current user (admins see every bot)."""
    query = db.query(Bot)
    if user.role != "admin":
        query = query.filter(Bot.user_id == user.id)
    return [_bot_out(b) for b in query.all()]


@router.post("", status_code=201, summary="Register a new grid bot")
def create_bot(
    payload: BotCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a bot record.  The three credential fields are Fernet-encrypted
    before being written to the database.
    """
    bot = Bot(
        user_id=user.id,
        symbol=payload.symbol,
        bitget_bot_id=payload.bitget_bot_id,
        api_key_enc=_encrypt(payload.api_key),
        api_secret_enc=_encrypt(payload.api_secret),
        passphrase_enc=_encrypt(payload.passphrase),
        config_json=json.dumps(payload.config),
        active=True,
        current_lower_price=payload.lower_price,
        current_upper_price=payload.upper_price,
        current_grid_num=payload.grid_num,
        current_invest_amount=payload.invest_amount,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)
    _scheduler.reload_jobs(SessionLocal)
    return _bot_out(bot)


@router.get("/{bot_id}", summary="Get a single bot")
def get_bot(
    bot_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bot = _get_bot_or_403(bot_id, user, db)
    return _bot_out(bot)


@router.put("/{bot_id}", summary="Update bot metadata")
def update_bot(
    bot_id: int,
    payload: BotUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Partially update a bot.  Only the fields supplied in the request body are
    changed.  Credentials cannot be updated through this endpoint.
    """
    bot = _get_bot_or_403(bot_id, user, db)

    if payload.symbol is not None:
        bot.symbol = payload.symbol
    if payload.bitget_bot_id is not None:
        bot.bitget_bot_id = payload.bitget_bot_id
    if payload.config is not None:
        bot.config_json = json.dumps(payload.config)
    if payload.active is not None:
        bot.active = payload.active

    db.commit()
    db.refresh(bot)
    _scheduler.reload_jobs(SessionLocal)
    return _bot_out(bot)


@router.delete("/{bot_id}", status_code=204, summary="Soft-delete a bot")
def delete_bot(
    bot_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark the bot inactive (soft delete) rather than removing the row."""
    bot = _get_bot_or_403(bot_id, user, db)
    bot.active = False
    db.commit()
    _scheduler.reload_jobs(SessionLocal)
    return None


@router.get("/{bot_id}/history", summary="Recent events for a bot")
def bot_history(
    bot_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the last *limit* Event rows for this bot, newest first."""
    _get_bot_or_403(bot_id, user, db)  # ownership check
    events = (
        db.query(Event)
        .filter(Event.bot_id == bot_id)
        .order_by(Event.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "before": e.before_json,
            "after": e.after_json,
            "created_at": e.created_at,
        }
        for e in events
    ]


@router.get("/{bot_id}/pnl", summary="PnL snapshots for charting")
def bot_pnl(
    bot_id: int,
    hours: int = Query(default=48, ge=1, le=8760),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return PnL snapshots taken in the last *hours* hours, ordered oldest first,
    suitable for rendering a time-series chart.
    """
    _get_bot_or_403(bot_id, user, db)  # ownership check
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(PnlSnapshot)
        .filter(PnlSnapshot.bot_id == bot_id, PnlSnapshot.created_at >= since)
        .order_by(PnlSnapshot.created_at.asc())
        .all()
    )
    return [
        {
            "ts": r.created_at,
            "total_pnl": r.total_pnl,
            "grid_profit": r.grid_profit,
            "floating_pnl": r.floating_pnl,
            "price": r.price,
            "invest_amount": r.invest_amount,
        }
        for r in rows
    ]


@router.post("/{bot_id}/test-connection", summary="Verify Bitget API credentials")
def test_connection(
    bot_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Decrypt the stored credentials, call Bitget to fetch bot PnL, and return the
    raw result.  No database rows are created or modified.
    """
    bot = _get_bot_or_403(bot_id, user, db)

    api_key = _decrypt(bot.api_key_enc)
    api_secret = _decrypt(bot.api_secret_enc)
    passphrase = _decrypt(bot.passphrase_enc)

    client = BitgetClient(api_key=api_key, api_secret=api_secret, passphrase=passphrase)
    try:
        result = client.get_ticker(bot.symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Bitget API error: {exc}") from exc

    return {"ok": True, "price": result["price"], "symbol": result["symbol"]}


@router.post("/{bot_id}/test-notification", summary="Send a test Google Chat notification")
def test_notification(
    bot_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bot = _get_bot_or_403(bot_id, user, db)
    cfg = json.loads(bot.config_json or "{}")
    webhook = cfg.get("gchat_webhook_url", "")
    if not webhook:
        raise HTTPException(status_code=400, detail="gchat_webhook_url not configured for this bot")
    from ..notifications.gchat import send
    ok = send(webhook, f"[TEST] Bitget Grid Optimizer — bot {bot.symbol} ({bot.bitget_bot_id}) connesso.")
    if not ok:
        raise HTTPException(status_code=502, detail="Notification delivery failed")
    return {"ok": True}
