FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends chromium xvfb x11-utils x11vnc fluxbox novnc websockify nginx ca-certificates fonts-liberation fonts-noto-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install-deps chromium && \
    python -m playwright install chromium
COPY . .
RUN mkdir -p /app/data && rm -rf /app/tests/__pycache__ /app/app/**/__pycache__ /app/.pytest_cache
EXPOSE 10000
CMD ["/app/start.sh"]
