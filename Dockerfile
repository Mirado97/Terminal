# ── Этап 1: сборка зависимостей ──────────────────────────────────────────────
FROM python:3.10-slim AS builder

WORKDIR /build

# Системные зависимости для asyncpg и hiredis
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir build && \
    pip install --no-cache-dir ".[dev]" --target /deps

# ── Этап 2: production image ──────────────────────────────────────────────────
FROM python:3.10-slim AS runtime

WORKDIR /app

# Только runtime системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Копируем зависимости из builder
COPY --from=builder /deps /usr/local/lib/python3.10/site-packages

# Исходный код
COPY . .

# Непривилегированный пользователь
RUN useradd -r -u 1001 -g root arb && \
    chown -R arb:root /app
USER arb

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production

EXPOSE 8080 9090

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')"

CMD ["python", "-m", "main"]
