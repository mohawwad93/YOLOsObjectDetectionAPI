# syntax=docker/dockerfile:1
# =============================================================================
# Stage 1: builder — everything needed to produce a virtual environment,
# none of which needs to survive into production.
# =============================================================================
FROM python:3.12-slim-bookworm AS builder

ARG TORCH_BACKEND=gpu

# uv as a single static binary, copied from Astral's official image rather
# than pip-installed — no bootstrapping pip just to install the tool that
# replaces it. `latest` is convenient here; pin to an explicit version tag
# (or digest) in real CI for the same reason Step 3 pinned exclude-newer —
# a moving build tool is itself a non-determinism source.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# --- Dependency layer: cached independently of source code (Part 1) ---
# Only the two files uv needs to resolve dependencies. Copying src/ here
# would invalidate this layer — and force a full torch/transformers
# reinstall — on every commit, regardless of what changed.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra ${TORCH_BACKEND}

# --- Application layer: invalidated only when source actually changes ---
COPY src/ ./src/
COPY README.md ./
# --no-editable: installs `backend` as a real package rather than a link
# back to this source tree, so the final stage can copy the virtual
# environment alone and leave the source code behind entirely.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra ${TORCH_BACKEND}

# =============================================================================
# Stage 2: production — a fresh pull, not `FROM builder`. uv, the build
# cache, and anything else from stage 1 are never candidates for inclusion
# unless explicitly COPY --from=builder'd below.
# =============================================================================
FROM python:3.12-slim-bookworm AS production

# Least-privilege: dedicated, unprivileged system user with a FIXED
# numeric UID/GID — what Kubernetes' runAsNonRoot/runAsUser securityContext
# checks actually compare against, independent of name resolution.
RUN groupadd --system --gid 1000 appuser \
    && useradd --system --uid 1000 --gid appuser --no-create-home appuser

WORKDIR /app

# Hugging Face's cache defaults to $HOME/.cache/huggingface — but $HOME
# resolves to /home/appuser, which --no-create-home deliberately never
# created, and appuser has no permission to create one under /home
# either. That mismatch is exactly what produced the PermissionError.
# Fix: point it at an explicit directory under /app instead, created and
# chowned here — more robust than depending on a home directory
# existing, and self-documenting about where the downloaded weights
# land (useful later if you want to mount this as a persistent volume
# to skip re-downloading on every pod restart).
ENV HF_HOME=/app/.cache/huggingface
RUN mkdir -p ${HF_HOME} && chown -R appuser:appuser /app

# Only the built virtual environment crosses the stage boundary — no uv
# binary, no build cache, no source distributions that got downloaded and
# compiled along the way.
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
# The frontend's static assets aren't part of the Python package (see the
# config.py fix above) — copied separately, to the absolute path we point
# APP_FRONTEND_DIR at below.
COPY --from=builder --chown=appuser:appuser /app/src/frontend /app/frontend

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_FRONTEND_DIR=/app/frontend

USER appuser

EXPOSE 8000

# uvicorn directly, not fastapi run — fastapi-cli wants a file path, and
# --no-editable deliberately means there is no source file in this image
# anymore, only the installed `backend` package. `backend.app:app` is a
# normal Python import, indifferent to where the source tree used to be.
# --workers 1 is stated explicitly, not left as an implicit default. Scale via
# Kubernetes replica count, not this flag.
# --proxy-headers: trust X-Forwarded-* from the Kubernetes ingress in
# front of this pod, so client IP/protocol info survives the hop —
# fastapi run enabled this by default; plain uvicorn needs it explicit.
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]