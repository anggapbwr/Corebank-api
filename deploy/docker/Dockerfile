# syntax=docker/dockerfile:1
# Multi-stage: deps/build terpisah dari runtime → image kecil & layer cache stabil.

# ---------- base ----------
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home /srv app

# ---------- builder: install dependencies + package ----------
FROM base AS builder
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .

# ---------- runtime: hanya apa yang dibutuhkan app ----------
FROM base AS runtime
COPY --from=builder /install /usr/local
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
# Liveness endpoint dipakai container healthcheck compose & K8s liveness probe nanti
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------- test: image terpisah untuk CI / `compose run app-test` ----------
FROM base AS test
COPY pyproject.toml README.md ./
COPY app ./app
COPY tests ./tests
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[dev]"
CMD ["pytest"]
