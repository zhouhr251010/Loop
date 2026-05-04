"""FastAPI application entry point for the Loop research platform."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401
from .database import Base, engine
from .routers import posts, simulate, users


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create local SQLite tables when the API starts."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Loop Research Platform API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(posts.router)
app.include_router(simulate.router)
app.include_router(users.router)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Return a simple health signal for local development and monitoring."""
    return {"status": "ok", "service": "loop-research-api"}


# Future RESTful routers will be mounted here.
