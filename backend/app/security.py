"""Authentication and API hardening helpers for Loop."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app import models
from app.crud import user as user_crud
from app.database import get_db

TokenPayload = dict[str, Any]

TOKEN_TTL_SECONDS = int(os.getenv("LOOP_ACCESS_TOKEN_TTL_SECONDS", "86400"))
MAX_REQUEST_BYTES = int(os.getenv("LOOP_MAX_REQUEST_BYTES", str(512 * 1024)))
RATE_LIMIT_REQUESTS = int(os.getenv("LOOP_RATE_LIMIT_REQUESTS", "120"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("LOOP_RATE_LIMIT_WINDOW_SECONDS", "60"))
TRUST_X_FORWARDED_FOR = os.getenv("LOOP_TRUST_X_FORWARDED_FOR", "").lower() in {
    "1",
    "true",
    "yes",
}

_runtime_secret = secrets.token_urlsafe(48)
_rate_limit_hits: dict[str, deque[float]] = defaultdict(deque)


def _secret_key() -> str:
    """Return the configured signing secret, or a per-process fallback."""
    return os.getenv("LOOP_AUTH_SECRET") or _runtime_secret


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _sign(message: str) -> str:
    digest = hmac.new(
        _secret_key().encode("utf-8"),
        message.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(digest)


def create_access_token(user: models.User) -> str:
    """Create a compact signed bearer token for one user."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    encoded_header = _b64url_encode(
        json.dumps(header, separators=(",", ":")).encode("utf-8"),
    )
    encoded_payload = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    signing_input = f"{encoded_header}.{encoded_payload}"
    return f"{signing_input}.{_sign(signing_input)}"


def verify_access_token(token: str) -> TokenPayload:
    """Verify token signature and expiry, returning its payload."""
    try:
        encoded_header, encoded_payload, signature = token.split(".", 2)
        signing_input = f"{encoded_header}.{encoded_payload}"
        expected_signature = _sign(signing_input)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Bad token signature.")

        payload = json.loads(_b64url_decode(encoded_payload))
        if not isinstance(payload, dict):
            raise ValueError("Bad token payload.")
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("Expired token.")
        return payload
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    """Resolve the authenticated user from the bearer token."""
    payload = verify_access_token(_extract_bearer_token(authorization))
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session subject.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    db_user = user_crud.get_user(db, user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session user no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return db_user


def require_same_user(user_id: int, current_user: models.User) -> None:
    """Reject access to another user's private research data."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own Loop data.",
        )


def require_admin_key(x_loop_admin_key: str | None = Header(default=None)) -> None:
    """Protect expensive simulation and research export endpoints."""
    configured_key = os.getenv("LOOP_ADMIN_API_KEY")
    if not configured_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API key is not configured.",
        )
    if not x_loop_admin_key or not hmac.compare_digest(x_loop_admin_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Valid X-Loop-Admin-Key is required.",
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add browser-facing hardening headers to every response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=()",
        )
        response.headers.setdefault("Cache-Control", "no-store")
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized JSON/body payloads before they reach route handlers."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": "Request body is too large."},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Invalid Content-Length header."},
                )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory per-client rate limiter for the MVP deployment."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path == "/health":
            return await call_next(request)

        client_ip = request.client.host if request.client else ""
        if TRUST_X_FORWARDED_FOR:
            forwarded_for = request.headers.get("x-forwarded-for", "")
            client_ip = forwarded_for.split(",", 1)[0].strip() or client_ip
        client_key = client_ip or "unknown"

        now = time.monotonic()
        hits = _rate_limit_hits[client_key]
        while hits and now - hits[0] > RATE_LIMIT_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests. Please slow down."},
                headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
            )
        hits.append(now)
        return await call_next(request)
