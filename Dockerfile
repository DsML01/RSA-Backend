FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 rsaapp \
    && useradd --uid 10001 --gid rsaapp --create-home --shell /usr/sbin/nologin rsaapp \
    && mkdir -p /data/uploads \
    && chown -R rsaapp:rsaapp /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R rsaapp:rsaapp /app

USER rsaapp
