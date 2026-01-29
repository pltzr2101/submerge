FROM python:3.11-slim

# ffmpeg pour extraction MKV (CLI manuelle)
# curl pour healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

# Installer uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/

# Installer les deps de base (sans ffsubsync qui est optionnel)
RUN uv pip install --system --no-cache .

# Health check standard 30s (les logs sont filtrés côté app)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:8282/health || exit 1

EXPOSE 8282
STOPSIGNAL SIGTERM
CMD ["submerge", "serve", "--host", "0.0.0.0", "--log-level", "info"]
