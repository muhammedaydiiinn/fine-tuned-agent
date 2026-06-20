import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import create_tables
from app.logging_config import configure_access_logging
from app.routes import (
    health,
    sessions,
    agent_turn,
    corrections,
    training,
    model_registry,
    evals,
    voice,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)
configure_access_logging()


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("CallShield Agent Backend starting — mode=%s", settings.vllm_mode)
    create_tables()
    logger.info("Database tables ready.")
    yield
    logger.info("CallShield Agent Backend shutting down.")


app = FastAPI(
    title="CallShield Agent Backend",
    version="1.0.0",
    description="CallShield Gold Paket sales agent platform",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# In production set CORS_ORIGINS in .env to the actual panel domain(s).
# allow_origins=["*"] cannot be combined with allow_credentials=True (browser
# rejects it), so we keep credentials False and rely on the API-key middleware.
_cors_origins: list[str] = (
    [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if settings.cors_origins
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API key guard ─────────────────────────────────────────────────────────────
@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """X-API-Key header check.

    Skipped when API_KEY env is empty (local dev).
    /health is always exempt for Docker healthcheck.
    """
    if not settings.api_key:
        return await call_next(request)

    if request.url.path in ("/health",):
        return await call_next(request)

    incoming_key = request.headers.get("X-API-Key", "")
    # Use secrets.compare_digest to prevent timing-based key enumeration.
    if not secrets.compare_digest(incoming_key, settings.api_key):
        logger.warning("Invalid API key — path=%s ip=%s", request.url.path, request.client.host)
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."})

    return await call_next(request)


# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch any unhandled exception, log with full traceback, return clean 500.

    FastAPI's built-in handler surfaces stack traces to the client; this one
    keeps internal details server-side.
    """
    logger.exception(
        "Unhandled exception — method=%s path=%s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(health.router, tags=["health"])
app.include_router(sessions.router, tags=["sessions"])
app.include_router(agent_turn.router, tags=["agent"])
app.include_router(corrections.router, tags=["corrections"])
app.include_router(training.router, tags=["training"])
app.include_router(model_registry.router, tags=["models"])
app.include_router(evals.router, tags=["evals"])
app.include_router(voice.router, tags=["voice"])
