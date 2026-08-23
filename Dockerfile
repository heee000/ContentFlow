ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS runtime

ARG UV_VERSION=0.11.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN useradd --create-home --uid 10001 contentflow

COPY pyproject.toml uv.lock ./
RUN pip install --upgrade pip "uv==${UV_VERSION}" \
    && uv sync --locked --no-dev --extra s3 --extra local-embeddings --no-install-project

COPY contentflow ./contentflow
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --locked --no-dev --extra s3 --extra local-embeddings --no-editable

RUN mkdir -p /app/.contentflow/storage /home/contentflow/.cache/huggingface \
    && chown -R contentflow:contentflow /app/.contentflow /home/contentflow/.cache

USER contentflow

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn contentflow.api:app --host 0.0.0.0 --port 8000"]
