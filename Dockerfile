# syntax=docker/dockerfile:1

# Astral recommends pinning uv for reproducible builds.
# https://docs.astral.sh/uv/guides/integration/docker
FROM ghcr.io/astral-sh/uv:0.12.8-python3.14-trixie-slim AS local

# Copy files instead of hard-linking when uv installs packages across filesystems.
# https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy 

WORKDIR /app

# Copy only dependency metadata first so Docker can cache dependency
# installation independently from application source-code changes.
# https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
COPY pyproject.toml uv.lock ./

# Install dependencies without project package to preserve cache.
# https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY . .

# Synchronize the environment again, now installing the project itself.
# Dependencies installed in the previous layer will normally be reused from cache.
# https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Use the project venv automatically. Reset the uv image entrypoint so CMD
# runs `langgraph` rather than being forwarded to `uv`.
# https://docs.astral.sh/uv/guides/integration/docker/#activating-a-venv
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT []

EXPOSE 2024

# https://docs.langchain.com/langsmith/cli
CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "2024", "--no-browser"]
