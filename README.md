# Bitget Grid Bot Optimizer

Automatically shifts and re-invests a Bitget Spot Grid Bot using ATR(14) + historical volatility (σ₂₀).  
Includes a web dashboard (Alpine.js + Chart.js), Google OAuth login, Google Chat notifications, and Docker deployment.

---

## How it works

Every configurable interval (default: 30 minutes) the optimizer:

1. **Fetches 50×1H candles** from Bitget for the configured symbol.
2. **Computes two indicators:**
   - `ATR(14)` — Wilder EMA of True Range (seeded with 14-period SMA)
   - `σ₂₀` — population standard deviation of the last 20 log-returns
3. **Checks TTM Squeeze guardrail** — if Bollinger Band width < Keltner Channel width the market is in compression; the shift is skipped to avoid being caught in a breakout.
4. **Calculates the optimal grid range:**
   ```
   half_range = max(2.5 × ATR14,  1.5 × σ₂₀ × price)
   upper = price + half_range
   lower = price - half_range
   ```
5. **Calculates grid count (geometric grid):**
   ```
   N = round( ln(upper / lower) / (step_target_pct / 100) )
   N = clamp(N, 2, max_grid_count)
   ```
6. **Decides whether to shift** — all three conditions must be true:
   - Price is within 5% of the current upper or lower boundary
   - The new range differs from the current range by > `shift_threshold_pct` (default 5%)
   - The cooldown period has elapsed since the last shift (default 60 min)
7. **Calls `POST /api/v2/spot/bot/modify-grid`** on Bitget if shifting.
8. **Saves a PnL snapshot** every cycle regardless of shift decision.
9. **Sends a Google Chat notification** on shift or TTM Squeeze skip.

A separate job (default every 6 hours) checks the spot USDT balance and adds free funds to the bot if the threshold is met (≥ 2 cells worth of USDT, max once per 24 hours).

---

## Grid type: geometric vs arithmetic

| | **Geometric** (recommended) | **Arithmetic** |
|---|---|---|
| Step size | Constant **%** of price | Constant USDT amount |
| Profit per cycle | Uniform across entire range | Smaller near top, larger near bottom |
| Best for | Trending + wide-range assets (BTC, ETH) | Stablecoins, very tight ranges |
| Bitget parameter | `gridType: geometric` | `gridType: arithmetic` |

**Geometric is strongly recommended** for BTC/USDT and similar volatile assets because every grid cell captures the same percentage move regardless of where price is in the range.

### Setting GRID_TYPE

**Option A — global default in `.env`:**
```env
GRID_TYPE=geometric     # or arithmetic
```

**Option B — per-bot in the web UI (Config tab → Optimizer parameters → Grid type):**  
Each bot stores its own `grid_type` inside the `config_json` column; the per-bot value always takes precedence over the global default.

**Option C — per-bot via API:**
```bash
curl -X PUT http://localhost:8000/api/bots/{bot_id} \
  -H "Cookie: session_token=<jwt>" \
  -H "Content-Type: application/json" \
  -d '{"config": {"grid_type": "geometric"}}'
```

> **Note:** Changing `grid_type` on a running bot takes effect on the *next shift*. Bitget applies the new type when `modify-grid` is called. No need to stop and recreate the bot — the API accepts `gridType` on every modify call.

---

## Requirements

- Python 3.12+
- A Bitget account with an **active Spot Grid Bot**
- Bitget API key with `Trade` + `Read` permissions (no withdrawal permission needed)
- (Optional) Google OAuth app for web UI login
- (Optional) Google Chat webhook URL for notifications

---

## Quick start — PoC (no database, no UI)

The PoC script lets you test the algorithm against your real bot without any setup beyond API keys.

```bash
# 1. Clone and enter backend
cd bitget/backend

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
cp ../.env.example ../.env
# Edit .env — fill in the five required fields (see below)

# 5. Dry run — prints the decision without touching Bitget
python -m app.poc --dry-run

# 6. Live run — calls modify-grid if shift is triggered
python -m app.poc
```

**Minimum `.env` for the PoC:**
```env
BITGET_API_KEY=your_api_key
BITGET_API_SECRET=your_api_secret
BITGET_PASSPHRASE=your_passphrase
BOT_ID=your_bot_id               # found in Bitget → Grid Bot → bot detail URL
SYMBOL=BTCUSDT
GRID_TYPE=geometric               # or arithmetic — must match your existing bot
```

**Example PoC output:**
```
═══════════════════════════════════════
 BITGET GRID BOT OPTIMIZER — DRY RUN
═══════════════════════════════════════
Symbol:          BTCUSDT
Current price:   $95,123.00

── Indicators ──────────────────────────
ATR(14):         $2,841.00
σ_20d:           1.87%
TTM Squeeze:     NO  ✓

── Optimal range ───────────────────────
Lower:           $87,982.00
Upper:           $102,264.00
Grid count:      19

── Current bot ─────────────────────────
Lower:           $80,000.00
Upper:           $100,000.00
Grid count:      15

── Decision ────────────────────────────
Shift:           YES — SHIFT_TRIGGERED
Delta upper:     2.26%
Delta lower:     9.98%

[DRY RUN — no changes executed]
═══════════════════════════════════════
```

---

## Full setup — Web UI + scheduler

### 1. Generate secrets

```bash
# Fernet encryption key (encrypt API keys in DB — generate once, never change)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT / session signing key
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill in **all** fields:

```env
# ── Bitget credentials ─────────────────────────────────────────────────────
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_PASSPHRASE=
BOT_ID=
SYMBOL=BTCUSDT

# ── Grid type ──────────────────────────────────────────────────────────────
GRID_TYPE=geometric          # geometric | arithmetic  (see section above)

# ── Optimizer ─────────────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES=30    # How often the optimizer runs per bot
SHIFT_THRESHOLD_PCT=5.0      # Min % change in range to trigger a shift
ATR_MULTIPLIER=2.5           # ATR weight in half_range formula
SIGMA_MULTIPLIER=1.5         # σ₂₀ weight in half_range formula
STEP_TARGET_PCT=0.8          # Target % step size per grid cell (0.8% recommended)
MAX_GRID_COUNT=150           # Bitget hard cap; reduce for narrow ranges
COOLDOWN_MINUTES=60          # Min time between shifts for the same bot
TTM_SQUEEZE_ENABLED=true     # Skip shifts during market compression
VOLATILITY_SPIKE_MULTIPLIER=1.5      # ATR spike detection threshold
VOLATILITY_SPIKE_RANGE_EXPAND=1.20   # Range expansion factor on ATR spike

# ── Fund manager ──────────────────────────────────────────────────────────
MIN_ADD_FUNDS_USDT=10.0      # Minimum USDT to add per reinvestment
FUND_CHECK_INTERVAL_HOURS=6  # How often the fund manager runs
RESERVE_PCT=2.0              # % of free USDT kept as reserve (not reinvested)

# ── Auth ──────────────────────────────────────────────────────────────────
SECRET_KEY=<generated above>
ADMIN_EMAIL=your@email.com   # First login with this email → admin role
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# ── Encryption ────────────────────────────────────────────────────────────
FERNET_KEY=<generated above>

# ── Database ──────────────────────────────────────────────────────────────
DATABASE_URL=sqlite:///./bitget.db   # dev; use postgresql://... in prod

# ── Notifications ─────────────────────────────────────────────────────────
GCHAT_WEBHOOK_URL=           # optional; leave empty to disable
```

### 3. Google OAuth setup (required for web UI login)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (Web application)
3. Add authorized redirect URI: `http://localhost:8000/auth/callback` (or your domain)
4. Copy **Client ID** and **Client Secret** into `.env`

### 4. Run locally

```bash
cd bitget/backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Open `frontend/dashboard.html` in a browser, or serve the `frontend/` folder with any static HTTP server.

---

## Docker deployment (production)

```bash
# 1. Fill in .env (production DATABASE_URL, real domain in GOOGLE redirect URI)
# 2. Build and start all services
docker-compose up -d

# 3. First-run SSL certificate (replace yourdomain.com)
docker-compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d yourdomain.com

# 4. Reload nginx
docker-compose exec nginx nginx -s reload

# 5. View backend logs
docker-compose logs -f backend
```

**Services started by `docker-compose up -d`:**

| Service | Description |
|---|---|
| `backend` | FastAPI + APScheduler (port 8000, internal) |
| `frontend` | nginx serving static HTML files |
| `db` | PostgreSQL 16 (port 5432, internal) |
| `nginx` | Reverse proxy, ports 80 + 443, TLS termination |
| `certbot` | Let's Encrypt certificate auto-renewal |

For production, set `DATABASE_URL=postgresql://bitget:password@db:5432/bitget` in `.env`.

---

## API reference

All endpoints under `/api/` require a valid `session_token` cookie (set after Google OAuth login).

### Bots

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/bots` | List bots for current user |
| `POST` | `/api/bots` | Register a new bot (credentials encrypted with Fernet) |
| `PUT` | `/api/bots/{id}` | Update symbol, config, or active flag |
| `DELETE` | `/api/bots/{id}` | Soft-delete (sets `active=false`) |
| `GET` | `/api/bots/{id}/history` | Last N events (`?limit=50`) |
| `GET` | `/api/bots/{id}/pnl` | PnL snapshots for chart (`?hours=48`) |
| `POST` | `/api/bots/{id}/test-connection` | Verify Bitget API credentials |
| `POST` | `/api/bots/{id}/test-notification` | Send test Google Chat message |

### Dashboard

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/dashboard/summary` | Global stats (PnL today, shifts today, …) |
| `GET` | `/api/dashboard/bots-status` | Per-bot cards with latest PnL |

### Admin (role=admin only)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/admin/users` | List all users |
| `PUT` | `/api/admin/users/{id}/toggle` | Enable / disable a user |
| `GET` | `/api/admin/metrics` | Global metrics across all bots |

---

## Event types

| Event | Trigger |
|---|---|
| `SHIFT_TRIGGERED` | Grid range was modified on Bitget |
| `TTM_SQUEEZE_SKIP` | Shift skipped — market in Bollinger Band compression |
| `FUNDS_ADDED` | Free USDT added to bot investment |
| `ERROR` | Any unhandled exception during optimize or fund-check cycle |

---

## Parameter tuning guide

| Parameter | Conservative | Aggressive | Notes |
|---|---|---|---|
| `ATR_MULTIPLIER` | 3.0 | 2.0 | Higher = wider range, fewer shifts |
| `SIGMA_MULTIPLIER` | 2.0 | 1.0 | Controls σ₂₀ weight |
| `STEP_TARGET_PCT` | 1.0 | 0.5 | Must stay > 2× Bitget fee (0.15% × 2 = 0.30%) |
| `SHIFT_THRESHOLD_PCT` | 8.0 | 3.0 | Lower = more shifts, more API calls |
| `COOLDOWN_MINUTES` | 120 | 30 | Prevents rapid oscillation |
| `MAX_GRID_COUNT` | 50 | 150 | Bitget allows 2–150 |
| `TTM_SQUEEZE_ENABLED` | true | false | Disable only for high-frequency scalping |

**Recommended starting point for BTC/USDT:** all defaults in `.env.example`.

---

## Project structure

```
bitget/
├── .env.example                     # Configuration template
├── docker-compose.yml
├── CLAUDE.md                        # Claude Code project rules
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                  # FastAPI entrypoint + lifespan
│       ├── config.py                # Pydantic Settings from env vars
│       ├── database.py              # SQLAlchemy engine + session factory
│       ├── models.py                # User, Bot, Event, PnlSnapshot
│       ├── poc.py                   # CLI dry-run / live test (no DB needed)
│       ├── core/
│       │   ├── bitget_client.py     # HMAC-SHA256 REST client (3× retry)
│       │   ├── indicators.py        # ATR14, σ₂₀, TTM Squeeze
│       │   ├── optimizer.py         # Grid range + shift decision engine
│       │   ├── fund_manager.py      # Auto-reinvest free USDT
│       │   └── scheduler.py         # APScheduler per-bot jobs
│       ├── api/
│       │   ├── auth.py              # Google OAuth2 + JWT cookie
│       │   ├── bots.py              # Bot CRUD + test endpoints
│       │   ├── dashboard.py         # Aggregated stats
│       │   └── admin.py             # Admin-only endpoints
│       └── notifications/
│           └── gchat.py             # Google Chat webhook
├── frontend/
│   ├── login.html
│   ├── dashboard.html               # Bot cards + PnL chart
│   ├── config.html                  # Per-bot parameter editor
│   └── admin.html                   # User management
└── nginx/
    └── nginx.conf
```

---

## Security notes

- API keys (Bitget) are encrypted with **Fernet AES-128** before being stored in the database and are never returned in any API response.
- The Fernet key must be generated once and **never changed** — rotating it requires re-encrypting all stored credentials.
- The `.env` file contains live secrets; never commit it to version control (it is listed in `.gitignore`).
- In production, set `https_only=True` for `SessionMiddleware` in `main.py` and serve exclusively over HTTPS.

---

## License

MIT
