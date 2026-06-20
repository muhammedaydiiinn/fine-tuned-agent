FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# HTMX'i CDN olmadan çalışabilmesi için build sırasında indir
RUN mkdir -p app/static && \
    curl -sL "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js" -o app/static/htmx.min.js

# LiveKit browser client is vendored so the supervisor test flow has no runtime CDN dependency.
RUN curl --fail --silent --show-error --location \
    "https://cdn.jsdelivr.net/npm/livekit-client@2.15.6/dist/livekit-client.umd.min.js" \
    -o app/static/livekit-client.umd.min.js

COPY app/ ./app/

EXPOSE 8020

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8020/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020"]
