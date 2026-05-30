# Multi-stage production Dockerfile for Hermes v2
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ──
FROM python:3.11-slim

RUN apt-get update && apt-get install -y libpq5 curl && rm -rf /var/lib/apt/lists/* && apt-get clean

RUN groupadd -r hermes && useradd -r -g hermes hermes

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

ENV PYTHONPATH=/app/backend

RUN chown -R hermes:hermes /app
USER hermes

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.api_main:app", "--host", "0.0.0.0", "--port", "8000"]
