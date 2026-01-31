FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/local/bin/playwright-browsers

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
