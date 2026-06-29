FROM nvidia/cuda:12.4.1-cudnn9-runtime-ubuntu22.04

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3.11 python3.11-venv libgomp1 \
    && python3.11 -m ensurepip --upgrade \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["python3.11", "-m", "app.worker", "start"]
