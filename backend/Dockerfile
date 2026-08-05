# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libpq-dev + gcc: needed to build psycopg (sync driver, used by Alembic/Celery).
# curl: used by the HEALTHCHECK.
# libexpat1, libgdal-dev, libgeos-c1v5, libproj-dev: runtime shared libraries
# the rasterio wheel's bundled GDAL links against for GeoTIFF/COG support
# (Master Plan §3 Phase 2 extensibility — python:3.12-slim omits these by
# default, so rasterio import fails at runtime without them even though pip
# install succeeds cleanly).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    curl \
    libexpat1 \
    libgdal-dev \
    libgeos-c1v5 \
    libproj-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY . .

RUN useradd --create-home --uid 1000 bodp && chown -R bodp:bodp /app
USER bodp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["gunicorn", "app.main:app", "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "120", \
     "--access-logfile", "-", "--error-logfile", "-"]
