# Training worker — Milestone 4'te Unsloth/LoRA eğitimi eklenecek
# GPU sunucuda host-native venv ile de çalıştırılabilir (Dockerfile alternatifi)
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
