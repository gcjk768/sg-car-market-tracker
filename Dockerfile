# Official Playwright image: Python, Chromium and every system library it needs.
# The tag must match the playwright version pinned in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Singapore

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Database, page cache and logs live on the mounted volumes.
RUN mkdir -p data logs

CMD ["python", "scheduler.py"]
