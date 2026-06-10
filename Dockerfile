FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config/ ./config/

ENV NTP_HOSTS_FILE=/app/config/hosts.txt \
    NTP_DB_PATH=/data/ntp_monitor.db \
    POLL_INTERVAL_SECONDS=60

EXPOSE 8000

VOLUME ["/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
