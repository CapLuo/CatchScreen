# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (aiortc requires libffi, libsrtp, libopus, libvpx via wheels; keep base minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Default ports
EXPOSE 5001 5002

# For production, you might swap to gunicorn/uvicorn; here keep simple
CMD ["python", "backend.py"]


