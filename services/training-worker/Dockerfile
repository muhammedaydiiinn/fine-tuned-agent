# Lightweight worker image for TRAINING_MODE=mock.
# For TRAINING_MODE=real, use a GPU host venv with requirements-gpu.txt and an
# Unsloth build matching the installed CUDA/Torch versions.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
