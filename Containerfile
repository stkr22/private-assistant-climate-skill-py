# Build stage: Python 3.12.12-trixie
FROM docker.io/library/python:3.12.12-trixie@sha256:872565c5ac89cafbab19419c699d80bda96e9d0f47a4790e5229bd3aeeeb5da9 AS build-python

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:0.9.5@sha256:f459f6f73a8c4ef5d69f4e6fbbdb8af751d6fa40ec34b39a1ab469acd6e289b7 /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependencies and pre-built wheel
COPY dist/*.whl /app/dist/

RUN --mount=type=cache,target=/root/.cache \
    uv venv && \
    uv pip install dist/*.whl

# runtime stage: Python 3.12.12-slim-trixie
FROM docker.io/library/python:3.12.12-slim-trixie@sha256:e97cf9a2e84d604941d9902f00616db7466ff302af4b1c3c67fb7c522efa8ed9

ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 --no-create-home --ingroup appuser appuser

WORKDIR /app
COPY --from=build-python /app /app

ENV PATH="/app/.venv/bin:$PATH"
# Set the user to 'appuser'
USER appuser

CMD ["private-assistant-climate-skill"]
