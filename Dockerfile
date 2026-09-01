FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CHROMIUM_PATH=/usr/bin/chromium \
    DISPLAY=:99

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    nginx \
    ca-certificates \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

# Fail the image build if the browser/runtime is missing. This prevents a
# successful Render deploy that later fails only when the user clicks Login.
RUN test -x /usr/bin/chromium \
    && /usr/bin/chromium --version \
    && python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print('Playwright chromium:', p.chromium.executable_path); p.stop()" \
    && test -x "$(python -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()')"

COPY . .
RUN mkdir -p /app/data /app/data/browser_profiles \
    && rm -rf /app/tests/__pycache__ /app/app/**/__pycache__ /app/.pytest_cache

EXPOSE 10000
CMD ["/app/start.sh"]
