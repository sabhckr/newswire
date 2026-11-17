# NEWSWIRE backend image: used by both the `api` and `worker` services.
# Debian trixie ships PostgreSQL 17 client tools, matching the database service (pg_dump must
# not be older than the server it dumps).
FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEWSWIRE_STORAGE_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /app newswire \
    && mkdir -p /data && chown newswire /data

WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.lock
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

USER newswire
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
