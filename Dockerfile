FROM python:3.11-slim

LABEL maintainer="pltzr2101" \
      version="2.1.0" \
      description="Bilingual subtitle merge service for ARR stacks"

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8282/health || exit 1

EXPOSE 8282
STOPSIGNAL SIGTERM
CMD ["submerge", "serve", "--host", "0.0.0.0", "--log-level", "info"]
