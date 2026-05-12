# Bitget Grid Bot Optimizer — Claude Instructions

## Project overview

Spot Grid Bot optimizer for Bitget. The backend (FastAPI + APScheduler) monitors active bots,
computes optimal grid ranges via ATR(14) + σ_20d, shifts the Bitget bot when triggered,
auto-reinvests free USDT, and exposes a REST API consumed by an Alpine.js + Chart.js frontend.

## Repository layout

```
bitget/
├── backend/app/
│   ├── main.py              # FastAPI entrypoint + lifespan (scheduler start/stop)
│   ├── config.py            # Pydantic Settings from env vars
│   ├── database.py          # SQLAlchemy engine, SessionLocal, init_db
│   ├── models.py            # User, Bot, Event, PnlSnapshot
│   ├── core/
│   │   ├── bitget_client.py # HMAC-SHA256 signed Bitget REST client
│   │   ├── indicators.py    # ATR14, σ_20d, TTM Squeeze (BB vs KC)
│   │   ├── optimizer.py     # Grid range + shift decision engine
│   │   ├── fund_manager.py  # Auto-reinvest free USDT logic
│   │   └── scheduler.py     # APScheduler: optimize + fund_check per bot
│   ├── api/
│   │   ├── auth.py          # Google OAuth2 + JWT session cookie
│   │   ├── bots.py          # Bot CRUD, history, PnL, test-connection
│   │   ├── dashboard.py     # Aggregated stats for UI cards
│   │   └── admin.py         # Admin-only: users list, global metrics
│   └── notifications/
│       └── gchat.py         # Google Chat webhook sender
├── frontend/                # Static HTML (login, dashboard, config, admin)
├── docker-compose.yml
└── .env.example             # Template — copy to .env and fill in secrets
```

## Development commands

```bash
# Install deps
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Run backend (dev)
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

# PoC dry-run (no live API calls)
cd backend && .venv/bin/python -m app.poc --dry-run

# PoC live
cd backend && .venv/bin/python -m app.poc

# Docker (production)
docker-compose up -d
```

## Architecture decisions

- **Geometric grid** (not arithmetic): equal % step per cell regardless of price level.
- **ATR(14) + σ_20d**: `half_range = max(2.5×ATR14, 1.5×σ_20d×price)`.
- **TTM Squeeze guardrail**: if `BB_width < KC_width` → skip shift, market compressed.
- **Fernet AES-128**: API keys encrypted at rest; never returned to callers.
- **APScheduler BackgroundScheduler**: one `optimize_{id}` + one `fund_{id}` job per bot.
- **SQLite (dev) → PostgreSQL (prod)**: controlled by `DATABASE_URL` env var.
- **Starlette SessionMiddleware** must be added **before** `CORSMiddleware` (authlib requirement).

## Coding standards

- Python 3.12, type-annotated function signatures.
- Pydantic v2 models for all API request/response schemas.
- SQLAlchemy sync ORM (not async) — `Session` from `get_db()` dependency.
- Never return encrypted credential columns (`api_key_enc`, `api_secret_enc`, `passphrase_enc`) in API responses.
- All timestamps stored as UTC; use `datetime.now(tz=timezone.utc)`.
- No `print()` in backend code — use `logging.getLogger(__name__)`.

## Security rules

- **Never read, display, or modify `.env`** — it contains live API keys and secrets.
  See `.env.example` for the template; direct the user there instead.
- Never log or return decrypted credentials (`api_key`, `api_secret`, `passphrase`).
- Never commit `.env` or any file containing real credentials.
- Fernet key rotation requires re-encrypting all `*_enc` columns — warn before touching encryption logic.

## Key environment variables (see `.env.example`)

| Variable | Purpose |
|---|---|
| `FERNET_KEY` | AES-128 key for credential encryption (generate once, never change) |
| `SECRET_KEY` | JWT + session signing key |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth2 app credentials |
| `ADMIN_EMAIL` | First login with this email gets role=admin |
| `DATABASE_URL` | `sqlite:////app/data/bitget.db` (dev) or `postgresql://…` (prod) |

## Testing

No automated test suite yet. Manual verification steps:

```bash
# Core algorithm (no API keys needed)
cd backend && .venv/bin/python -c "
from app.core.indicators import Candle, compute_indicators
from app.core.optimizer import BotConfig, BotState, decide
print('imports ok')
"

# PoC dry-run against real Bitget API
.venv/bin/python -m app.poc --dry-run
```

## Bitget API endpoints used

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v2/spot/market/candles` | Fetch OHLCV candles |
| GET | `/api/v2/spot/bot/bot-detail` | Current bot state |
| POST | `/api/v2/spot/bot/modify-grid` | Shift grid range |
| GET | `/api/v2/spot/account/assets` | Spot wallet balance |
| GET | `/api/v2/spot/bot/history-bot-pnl` | Bot PnL history |
