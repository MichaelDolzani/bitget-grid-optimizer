"""
Google OAuth2 login flow using authlib + JWT session cookie.

Cookie name  : session_token (httponly, samesite=lax, 7-day TTL)
JWT algorithm: HS256 signed with settings.secret_key
First-time login with settings.admin_email gets role="admin"; everyone else gets role="user".

NOTE: main.py must add starlette.middleware.sessions.SessionMiddleware before this
      router is reached — authlib uses the Starlette session store for the OAuth state
      parameter.  That middleware is already wired in main.py.
"""

from datetime import datetime, timedelta

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User

router = APIRouter()

# ---------------------------------------------------------------------------
# OAuth client registration
# ---------------------------------------------------------------------------

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_ALGORITHM = "HS256"
_COOKIE_NAME = "session_token"
_COOKIE_MAX_AGE = 7 * 24 * 3600  # seconds


def create_jwt(user_id: int) -> str:
    """Return a signed JWT containing the user's database id."""
    expire = datetime.utcnow() + timedelta(days=7)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


# ---------------------------------------------------------------------------
# Auth dependency — used by all protected routes
# ---------------------------------------------------------------------------

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Read the JWT from the session_token cookie and return the matching active
    User row.  Raises HTTP 401 on any failure.
    """
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id, User.active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/login", summary="Redirect to Google OAuth consent screen")
async def login(request: Request):
    return await oauth.google.authorize_redirect(request, settings.google_redirect_uri)


@router.get("/callback", summary="Google OAuth2 callback")
async def callback(request: Request, db: Session = Depends(get_db)):
    """
    Exchange the authorization code for tokens, upsert the User row, set the
    JWT cookie, and redirect to the dashboard.
    """
    token = await oauth.google.authorize_access_token(request)

    # authlib >= 1.0 places parsed userinfo directly on the token dict
    userinfo = token.get("userinfo") or await oauth.google.userinfo(token=token)

    google_id: str = userinfo["sub"]
    email: str = userinfo["email"]

    user = db.query(User).filter(User.google_id == google_id).first()
    if not user:
        role = "admin" if email == settings.admin_email else "user"
        user = User(google_id=google_id, email=email, role=role, active=True)
        db.add(user)
        db.commit()
        db.refresh(user)

    response = RedirectResponse(url="/dashboard.html")
    response.set_cookie(
        key=_COOKIE_NAME,
        value=create_jwt(user.id),
        httponly=True,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
    )
    return response


@router.get("/me", summary="Return the currently authenticated user")
def me(user: User = Depends(get_current_user)):
    """Return basic profile information for the logged-in user."""
    return {"id": user.id, "email": user.email, "role": user.role}


@router.get("/logout", summary="Clear session cookie and redirect to home")
def logout():
    response = RedirectResponse(url="/login.html")
    response.delete_cookie(_COOKIE_NAME)
    return response
