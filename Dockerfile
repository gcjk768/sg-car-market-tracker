# Official Playwright image: Python, Chromium and every system library it needs.
# The tag must match the playwright version pinned in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Singapore

WORKDIR /app

# Optional: the Claude Code CLI for the ai section of config.yaml. Build with
#   docker compose build --build-arg WITH_CLAUDE=1
# and put CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY in .env.
ARG WITH_CLAUDE=0
RUN if [ "$WITH_CLAUDE" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
      && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
      && apt-get install -y --no-install-recommends nodejs \
      && npm install -g @anthropic-ai/claude-code \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Database, page cache, logs and the Claude CLI login live on the mounted volumes.
RUN mkdir -p data logs /root/.claude

CMD ["python", "scheduler.py"]
