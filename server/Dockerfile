# Companion server image: FastAPI app + Claude Code CLI (subscription auth).
# Built by CI; not buildable-tested on the dev Mac (no docker).
FROM python:3.12-slim

# rsync/ssh for pulling documents from the tablet; node for the Claude Code CLI.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg rsync openssh-client \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app

# Non-root; HOME lives on the data PVC at runtime so Claude Code sessions,
# ssh known_hosts, and our sqlite state all persist across restarts.
RUN useradd --uid 1000 --create-home app
USER 1000
ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/data/home \
    REMARQUE_DATA_DIR=/data/remarque

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
