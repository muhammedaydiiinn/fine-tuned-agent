"""Supervisor Panel — Milestone 2'de tam hale getirilecek.

Şu an: /health + basit hoş geldiniz sayfası.
"""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

app = FastAPI(title="Anrufblocker Supervisor Panel", version="0.1.0")

# Template klasörü Milestone 2'de oluşturulacak
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/health")
def health():
    return {"status": "ok", "service": "supervisor-panel", "milestone": "2-pending"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    html = """
    <!doctype html>
    <html lang="tr">
    <head><meta charset="utf-8"><title>Anrufblocker Supervisor Panel</title>
    <style>body{font-family:sans-serif;max-width:600px;margin:80px auto;text-align:center}
    .badge{background:#e0f2fe;color:#0369a1;padding:4px 12px;border-radius:999px;font-size:.85rem}</style>
    </head>
    <body>
    <h1>Anrufblocker Supervisor Panel</h1>
    <p><span class="badge">Milestone 2 — Geliştirme aşamasında</span></p>
    <p>Bu panel Milestone 2'de FastAPI + Jinja2 + HTMX ile tamamlanacak.</p>
    <p>Backend API: <a href="http://localhost:8010/health">localhost:8010/health</a></p>
    <p>API Docs: <a href="http://localhost:8010/docs">localhost:8010/docs</a></p>
    </body></html>
    """
    return HTMLResponse(content=html)
