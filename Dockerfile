FROM python:3.11-slim

LABEL maintainer="pltzr2101" \
      version="2.0.3" \
      description="Bilingual subtitle merge service for ARR stacks"

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache -r pyproject.toml
COPY src/ src/
RUN uv pip install --system --no-cache --no-deps .

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8282/health || exit 1

EXPOSE 8282
STOPSIGNAL SIGTERM
CMD ["submerge", "serve", "--host", "0.0.0.0", "--log-level", "info"]
