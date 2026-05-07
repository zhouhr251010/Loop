"""FastAPI application entry point for the Loop research platform."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import models  # noqa: F401
from .database import Base, engine, ensure_sqlite_schema
from .routers import chat, export, memory, posts, simulate, users
from .security import (
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
DEFAULT_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]


def get_cors_origins() -> list[str]:
    """Read comma-separated frontend origins for local or remote development."""
    configured_origins = os.getenv("BACKEND_CORS_ORIGINS", "")
    origins = [
        origin.strip()
        for origin in configured_origins.split(",")
        if origin.strip()
    ]
    return origins or DEFAULT_CORS_ORIGINS


def get_allowed_hosts() -> list[str]:
    """Read comma-separated Host header values accepted by the API."""
    configured_hosts = os.getenv("LOOP_ALLOWED_HOSTS", "")
    hosts = [
        host.strip()
        for host in configured_hosts.split(",")
        if host.strip()
    ]
    return hosts or DEFAULT_ALLOWED_HOSTS


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create local SQLite tables when the API starts."""
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema()
    yield


app = FastAPI(title="Loop Research Platform API", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_allowed_hosts())
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Loop-Admin-Key"],
)

app.include_router(posts.router)
app.include_router(simulate.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(export.router)
app.include_router(users.router)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Return a simple health signal for local development and monitoring."""
    return {"status": "ok", "service": "loop-research-api"}


# Future RESTful routers will be mounted here.
