from datetime import datetime
from sqlalchemy import String, Float, Integer, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(256), unique=True)
    role: Mapped[str] = mapped_column(String(16), default="user")  # "user" | "admin"
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    bots: Mapped[list["Bot"]] = relationship("Bot", back_populates="user")


class Bot(Base):
    __tablename__ = "bots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    bitget_bot_id: Mapped[str] = mapped_column(String(64))
    api_key_enc: Mapped[str] = mapped_column(Text)
    api_secret_enc: Mapped[str] = mapped_column(Text)
    passphrase_enc: Mapped[str] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text, default="{}")  # BotConfig as JSON
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_shift_ts: Mapped[float] = mapped_column(Float, default=0.0)
    # Current bot grid state — authoritative source after first shift; set on bot creation
    current_lower_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_upper_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_grid_num: Mapped[int] = mapped_column(Integer, default=0)
    current_invest_amount: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user: Mapped["User"] = relationship("User", back_populates="bots")
    events: Mapped[list["Event"]] = relationship("Event", back_populates="bot")
    pnl_snapshots: Mapped[list["PnlSnapshot"]] = relationship("PnlSnapshot", back_populates="bot")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(Integer, ForeignKey("bots.id"))
    event_type: Mapped[str] = mapped_column(String(32))  # SHIFT_TRIGGERED, FUNDS_ADDED, TTM_SQUEEZE_SKIP, ERROR
    before_json: Mapped[str] = mapped_column(Text, default="{}")
    after_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    bot: Mapped["Bot"] = relationship("Bot", back_populates="events")


class PnlSnapshot(Base):
    __tablename__ = "pnl_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(Integer, ForeignKey("bots.id"))
    total_pnl: Mapped[float] = mapped_column(Float)
    grid_profit: Mapped[float] = mapped_column(Float)
    floating_pnl: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    invest_amount: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    bot: Mapped["Bot"] = relationship("Bot", back_populates="pnl_snapshots")
