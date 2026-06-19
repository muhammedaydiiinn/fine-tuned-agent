import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import create_tables
from app.routes import health, sessions, agent_turn, corrections, training, model_registry, evals

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="CallShield Agent Backend",
    version="1.0.0",
    description="CallShield Gold Paket sales agent platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to supervisor panel domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    if incoming_key != settings.api_key:
        logger.warning("Invalid API key — path=%s ip=%s", request.url.path, request.client.host)
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."})

    return await call_next(request)


@app.on_event("startup")
def on_startup():
    logger.info("CallShield Agent Backend starting — mode=%s", settings.vllm_mode)
    create_tables()
    logger.info("Database tables ready.")


# ── Register routes ───────────────────────────────────────────────────────────
app.include_router(health.router, tags=["health"])
app.include_router(sessions.router, tags=["sessions"])
app.include_router(agent_turn.router, tags=["agent"])
app.include_router(corrections.router, tags=["corrections"])
app.include_router(training.router, tags=["training"])
app.include_router(model_registry.router, tags=["models"])
app.include_router(evals.router, tags=["evals"])
