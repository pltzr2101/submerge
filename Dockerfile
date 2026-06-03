FROM python:3.11-slim

LABEL maintainer="pltzr2101" \
      version="2.1.3" \
      description="Bilingual subtitle merge service for ARR stacks"

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PATH="/app/.venv/bin:$PATH"
# Guarantees src-layout resolution; complements uv pip install --no-deps
ENV PYTHONPATH="/app/src"

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8282/health || exit 1

EXPOSE 8282
STOPSIGNAL SIGTERM
ENTRYPOINT ["/app/.venv/bin/python", "-m", "submerge.cli"]
CMD ["serve", "--host", "0.0.0.0", "--log-level", "info"]
