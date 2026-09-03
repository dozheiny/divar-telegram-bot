FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && pip install --no-cache-dir requests \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY crontab /etc/cron.d/crontab
COPY main.py run.sh tokens.json /app/
RUN chmod 0644 /etc/cron.d/crontab \
    && chmod +x /app/run.sh \
    && crontab /etc/cron.d/crontab

ENTRYPOINT ["/app/run.sh"]
CMD ["cron", "-f", "-l", "2"]
