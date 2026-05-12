"""
Dashboard summary and per-bot status endpoints.

/api/dashboard/summary     — aggregate stats for the logged-in user
/api/dashboard/bots-status — per-bot latest PnL snapshot + last event
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..api.auth import get_current_user
from ..database import get_db
from ..models import Bot, Event, PnlSnapshot, User

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_start() -> datetime:
    """Return midnight UTC for today as a timezone-aware datetime."""
    now = datetime.now(tz=timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _get_user_bot_ids(user: User, db: Session) -> list[int]:
    """Return bot IDs accessible to this user (all for admin, own for others)."""
    query = db.query(Bot.id)
    if user.role != "admin":
        query = query.filter(Bot.user_id == user.id)
    return [row[0] for row in query.all()]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/summary", summary="Aggregate stats for the current user")
def summary(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return:
      - total_bots / active_bots
      - total_pnl_today  — sum of total_pnl from PnlSnapshot rows created today
      - shifts_today     — count of SHIFT_TRIGGERED events today
      - funds_added_today — count of FUNDS_ADDED events today
    """
    today = _today_start()
    bot_ids = _get_user_bot_ids(user, db)

    if not bot_ids:
        return {
            "total_bots": 0,
            "active_bots": 0,
            "total_pnl_today": 0.0,
            "shifts_today": 0,
            "funds_added_today": 0,
        }

    # Bot counts
    total_bots = len(bot_ids)
    active_bots = (
        db.query(Bot)
        .filter(Bot.id.in_(bot_ids), Bot.active == True)
        .count()
    )

    # PnL today — use the most recent snapshot per bot created today
    pnl_today_rows = (
        db.query(PnlSnapshot)
        .filter(PnlSnapshot.bot_id.in_(bot_ids), PnlSnapshot.created_at >= today)
        .all()
    )
    total_pnl_today = sum(r.total_pnl or 0.0 for r in pnl_today_rows)

    # Event counts today
    shifts_today = (
        db.query(Event)
        .filter(
            Event.bot_id.in_(bot_ids),
            Event.event_type == "SHIFT_TRIGGERED",
            Event.created_at >= today,
        )
        .count()
    )
    funds_added_today = (
        db.query(Event)
        .filter(
            Event.bot_id.in_(bot_ids),
            Event.event_type == "FUNDS_ADDED",
            Event.created_at >= today,
        )
        .count()
    )

    return {
        "total_bots": total_bots,
        "active_bots": active_bots,
        "total_pnl_today": round(total_pnl_today, 4),
        "shifts_today": shifts_today,
        "funds_added_today": funds_added_today,
    }


@router.get("/bots-status", summary="Per-bot status with latest PnL and last event")
def bots_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    For each bot accessible to the current user, return:
      - core bot fields (id, symbol, active, grid config from config_json)
      - the latest PnlSnapshot values (total_pnl, grid_profit, floating_pnl,
        price, invest_amount)
      - the most recent Event type and timestamp
    """
    bot_query = db.query(Bot)
    if user.role != "admin":
        bot_query = bot_query.filter(Bot.user_id == user.id)
    bots = bot_query.all()

    if not bots:
        return []

    bot_ids = [b.id for b in bots]

    # Latest PnlSnapshot per bot
    # Subquery: max(id) per bot_id as a proxy for most-recent row
    from sqlalchemy import func

    latest_snap_ids = (
        db.query(func.max(PnlSnapshot.id))
        .filter(PnlSnapshot.bot_id.in_(bot_ids))
        .group_by(PnlSnapshot.bot_id)
        .subquery()
    )
    snapshots_by_bot: dict[int, PnlSnapshot] = {}
    for snap in db.query(PnlSnapshot).filter(PnlSnapshot.id.in_(latest_snap_ids)).all():
        snapshots_by_bot[snap.bot_id] = snap

    # Latest Event per bot
    latest_event_ids = (
        db.query(func.max(Event.id))
        .filter(Event.bot_id.in_(bot_ids))
        .group_by(Event.bot_id)
        .subquery()
    )
    events_by_bot: dict[int, Event] = {}
    for evt in db.query(Event).filter(Event.id.in_(latest_event_ids)).all():
        events_by_bot[evt.bot_id] = evt

    result = []
    for bot in bots:
        cfg: dict = (
            bot.config_json
            if isinstance(bot.config_json, dict)
            else json.loads(bot.config_json or "{}")
        )
        snap = snapshots_by_bot.get(bot.id)
        evt = events_by_bot.get(bot.id)

        result.append(
            {
                "bot_id": bot.id,
                "symbol": bot.symbol,
                "active": bot.active,
                "lower_price": bot.current_lower_price,
                "upper_price": bot.current_upper_price,
                "grid_num": bot.current_grid_num,
                "invest_amount": bot.current_invest_amount,
                # Latest financials
                "total_pnl": snap.total_pnl if snap else None,
                "grid_profit": snap.grid_profit if snap else None,
                "floating_pnl": snap.floating_pnl if snap else None,
                "price": snap.price if snap else None,
                # Last event
                "last_event_type": evt.event_type if evt else None,
                "last_event_ts": evt.created_at if evt else None,
            }
        )

    return result
