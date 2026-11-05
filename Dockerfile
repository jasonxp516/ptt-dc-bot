FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/bot_data.db \
    LOG_PATH=/app/data/bot.log \
    LOG_TO_STDOUT=1

WORKDIR /app

# 安裝編譯 lxml 所需之最小系統相依套件並建立非 root 執行帳號 (UID 1000)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 botuser \
    && mkdir -p /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# V-06 Hardening: Application code is owned by root:root (0755), only /app/data is writable by botuser (0700)
USER root
RUN chown -R root:root /app && \
    chmod -R 755 /app && \
    chown -R botuser:botuser /app/data && \
    chmod 700 /app/data

USER botuser

VOLUME ["/app/data"]

CMD ["python", "bot.py"]
