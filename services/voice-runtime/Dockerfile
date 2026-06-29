FROM nvidia/cuda:12.9.2-cudnn-runtime-ubuntu24.04

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip libgomp1 \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --break-system-packages: Docker container is isolated, no venv needed
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app ./app

CMD ["python3", "-m", "app.worker", "start"]
