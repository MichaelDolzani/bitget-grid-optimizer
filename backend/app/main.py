import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware  # Required by authlib OAuth flow
from .database import init_db, SessionLocal
from .core import scheduler
from .api import auth, bots, dashboard, admin
from .config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.fernet_key:
        scheduler.start(SessionLocal)
    yield
    scheduler.stop()


app = FastAPI(title="Bitget Grid Optimizer", lifespan=lifespan)

# SessionMiddleware must come before CORS so authlib can use the session store
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="oauth_session",
    https_only=False,  # Set True in production behind TLS
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(bots.router, prefix="/api/bots", tags=["bots"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
