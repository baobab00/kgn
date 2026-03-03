# ── KGN CLI Docker image ───────────────────────────────────────────
# Multi-stage build: builder (uv + build) → runtime (slim + wheel)
#
# Build:
#   docker build -f docker/Dockerfile -t kgn .
#
# Run:
#   docker run --rm kgn --version
#   docker run --rm --env-file .env kgn init --project my-project

# ── Stage 1: Builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml README.md LICENSE kgn/__init__.py ./
COPY kgn/ kgn/

# Build wheel
RUN uv build --wheel --out-dir /build/dist

# ── Stage 2: Runtime ─────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="Haenam Park"
LABEL description="KGN — Knowledge Graph CLI for AI agents"
LABEL org.opencontainers.image.source="https://github.com/baobab00/kgn"

# Install only the wheel (no build tools in final image)
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

# Create non-root user
RUN useradd --create-home --shell /bin/bash kgn
USER kgn
WORKDIR /home/kgn

ENTRYPOINT ["kgn"]
CMD ["--help"]
