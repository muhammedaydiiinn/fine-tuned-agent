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
    description="CallShield Gold Paket satış ajanı platformu",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Prod'da supervisor panel domain'ini kısıtla
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """X-API-Key header kontrolü.

    API_KEY env boşsa atlanır (local dev).
    /health endpoint'i her zaman muaf — Docker healthcheck için.
    """
    if not settings.api_key:
        return await call_next(request)

    if request.url.path in ("/health",):
        return await call_next(request)

    incoming_key = request.headers.get("X-API-Key", "")
    if incoming_key != settings.api_key:
        logger.warning("Geçersiz API key — path=%s ip=%s", request.url.path, request.client.host)
        return JSONResponse(status_code=401, content={"detail": "Geçersiz veya eksik API key."})

    return await call_next(request)


@app.on_event("startup")
def on_startup():
    logger.info("CallShield Agent Backend başlatılıyor — mode=%s", settings.vllm_mode)
    create_tables()
    logger.info("Veritabanı tabloları hazır.")


# ── Route'ları kaydet ────────────────────────────────────────────────────────
app.include_router(health.router, tags=["health"])
app.include_router(sessions.router, tags=["sessions"])
app.include_router(agent_turn.router, tags=["agent"])
app.include_router(corrections.router, tags=["corrections"])
app.include_router(training.router, tags=["training"])
app.include_router(model_registry.router, tags=["models"])
app.include_router(evals.router, tags=["evals"])
