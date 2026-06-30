FROM python:3.11-slim

LABEL maintainer="pltzr2101" \
      version="2.1.3" \
      description="Bilingual subtitle merge service for ARR stacks"

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Install alass for SRT-to-SRT subtitle synchronization
# (pre-built static binary from GitHub releases)
ARG ALASS_VERSION=2.0.0
ARG ALASS_SHA256="7bd0b9ae7e035d3ba940eacffb21243614df36231d47f21f0b4ce42001ab7fcd"
RUN curl -fsSL -o /usr/local/bin/alass \
    "https://github.com/kaegi/alass/releases/download/v${ALASS_VERSION}/alass-linux64" && \
    echo "${ALASS_SHA256}  /usr/local/bin/alass" | sha256sum -c - && \
    chmod +x /usr/local/bin/alass

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
