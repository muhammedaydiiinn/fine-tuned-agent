import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import PUBLIC_PATHS, is_authenticated
from app.config import settings
from app.routes import auth, corrections, evals, registry, review, sessions, training, turns

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Anrufblocker Supervisor Panel", version="1.0.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router, tags=["auth"])
app.include_router(sessions.router, tags=["sessions"])
app.include_router(turns.router, tags=["turns"])
app.include_router(corrections.router, tags=["corrections"])
app.include_router(training.router, tags=["training"])
app.include_router(evals.router, tags=["evals"])
app.include_router(registry.router, tags=["registry"])
app.include_router(review.router, tags=["review"])


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok", "service": "supervisor-panel"}
