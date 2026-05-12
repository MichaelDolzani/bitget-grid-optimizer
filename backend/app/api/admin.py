"""
Admin-only endpoints.

All routes in this module require the caller to have role="admin".  A shared
`require_admin` dependency enforces this; passing a non-admin JWT raises HTTP 403.

Routes
------
GET  /api/admin/users              — list all users with bot count and last event
PUT  /api/admin/users/{id}/toggle  — toggle a user's active flag (soft ban)
GET  /api/admin/metrics            — global platform statistics
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..api.auth import get_current_user
from ..database import get_db
from ..models import Bot, Event, User

router = APIRouter()


# ---------------------------------------------------------------------------
# Admin dependency
# ---------------------------------------------------------------------------

def require_admin(user: User = Depends(get_current_user)) -> User:
    """
    FastAPI dependency that passes through the current user only when they hold
    the "admin" role.  Raises HTTP 403 for all other roles.
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_start() -> datetime:
    """Return midnight UTC today as a timezone-aware datetime."""
    now = datetime.now(tz=timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/users", summary="List all users with activity summary")
def list_users(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Return every user with:
      - id, email, role, active
      - bot_count   — total number of bot rows for this user
      - last_event_ts — timestamp of the most recent event across all their bots
    """
    users = db.query(User).order_by(User.id).all()
    if not users:
        return []

    user_ids = [u.id for u in users]

    # Bot count per user
    bot_counts: dict[int, int] = {
        row[0]: row[1]
        for row in db.query(Bot.user_id, func.count(Bot.id))
        .filter(Bot.user_id.in_(user_ids))
        .group_by(Bot.user_id)
        .all()
    }

    # Latest event timestamp per user — join Bot to get user_id
    last_event_rows = (
        db.query(Bot.user_id, func.max(Event.created_at))
        .join(Event, Event.bot_id == Bot.id)
        .filter(Bot.user_id.in_(user_ids))
        .group_by(Bot.user_id)
        .all()
    )
    last_event_by_user: dict[int, datetime] = {
        row[0]: row[1] for row in last_event_rows
    }

    return [
        {
            "id": u.id,
            "email": u.email,
            "role": u.role,
            "active": u.active,
            "bot_count": bot_counts.get(u.id, 0),
            "last_event_ts": last_event_by_user.get(u.id),
        }
        for u in users
    ]


@router.put("/users/{user_id}/toggle", summary="Toggle a user's active status")
def toggle_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Flip the active flag for the target user.  An admin cannot deactivate their
    own account to prevent accidental lockout.
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot deactivate your own account",
        )

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.active = not target.active
    db.commit()
    db.refresh(target)

    return {
        "id": target.id,
        "email": target.email,
        "active": target.active,
        "message": f"User {'activated' if target.active else 'deactivated'} successfully",
    }


@router.get("/metrics", summary="Global platform statistics")
def global_metrics(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Return platform-wide aggregate counts:
      - total_users / total_bots / active_bots
      - shifts_today      — SHIFT_TRIGGERED events today
      - funds_added_today — FUNDS_ADDED events today
      - errors_today      — ERROR events today
    """
    today = _today_start()

    total_users = db.query(func.count(User.id)).scalar() or 0
    total_bots = db.query(func.count(Bot.id)).scalar() or 0
    active_bots = (
        db.query(func.count(Bot.id)).filter(Bot.active == True).scalar() or 0
    )

    def _event_count_today(event_type: str) -> int:
        return (
            db.query(func.count(Event.id))
            .filter(Event.event_type == event_type, Event.created_at >= today)
            .scalar()
            or 0
        )

    return {
        "total_users": total_users,
        "total_bots": total_bots,
        "active_bots": active_bots,
        "shifts_today": _event_count_today("SHIFT_TRIGGERED"),
        "funds_added_today": _event_count_today("FUNDS_ADDED"),
        "errors_today": _event_count_today("ERROR"),
    }
