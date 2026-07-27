ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 contentflow

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && mkdir -p contentflow \
    && touch contentflow/__init__.py \
    && pip install ".[postgres,s3]" \
    && rm -rf contentflow

COPY contentflow ./contentflow
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-deps .

RUN mkdir -p /app/.contentflow/storage \
    && chown -R contentflow:contentflow /app

USER contentflow

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn contentflow.api:app --host 0.0.0.0 --port 8000"]
