FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Static assets (htmx, livekit-client) are vendored in app/static/ so the build
# has no runtime CDN dependency. If the files are missing, run:
#   curl -sL "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js" \
#        -o services/supervisor-panel/app/static/htmx.min.js
#   curl -sL "https://cdn.jsdelivr.net/npm/livekit-client@2.15.6/dist/livekit-client.umd.min.js" \
#        -o services/supervisor-panel/app/static/livekit-client.umd.min.js
COPY app/ ./app/

EXPOSE 8020

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8020/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020"]
