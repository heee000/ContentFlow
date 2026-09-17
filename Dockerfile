ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS runtime

ARG UV_VERSION=0.11.2
ARG INCLUDE_LOCAL_EMBEDDINGS=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN useradd --create-home --uid 10001 contentflow

COPY pyproject.toml uv.lock ./
RUN pip install --upgrade pip "uv==${UV_VERSION}" \
    && case "${INCLUDE_LOCAL_EMBEDDINGS}" in true|false) ;; *) echo 'INCLUDE_LOCAL_EMBEDDINGS must be true or false' >&2; exit 1 ;; esac \
    && if [ "${INCLUDE_LOCAL_EMBEDDINGS}" = true ]; then \
         uv sync --locked --no-dev --extra s3 --extra local-embeddings --no-install-project; \
       else \
         uv sync --locked --no-dev --extra s3 --no-install-project; \
       fi

COPY contentflow ./contentflow
COPY alembic.ini ./
COPY migrations ./migrations
RUN if [ "${INCLUDE_LOCAL_EMBEDDINGS}" = true ]; then \
      uv sync --locked --no-dev --extra s3 --extra local-embeddings --no-editable; \
    else \
      uv sync --locked --no-dev --extra s3 --no-editable; \
    fi

LABEL io.contentflow.local-embeddings="${INCLUDE_LOCAL_EMBEDDINGS}"

RUN mkdir -p /app/.contentflow/storage /home/contentflow/.cache/huggingface \
    && chown -R contentflow:contentflow /app/.contentflow /home/contentflow/.cache

USER contentflow

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn contentflow.api:app --host 0.0.0.0 --port 8000"]
