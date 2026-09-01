import asyncio
import logging
import os
import shutil
from pathlib import Path
from urllib.parse import quote_plus

from app.config import get_settings

log = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover
    async_playwright = None

LOGIN_URLS = {
    "stepstone": "https://www.stepstone.de/login",
    "indeed": "https://de.indeed.com/account/login",
    "linkedin": "https://www.linkedin.com/login",
    "xing": "https://login.xing.com/",
    "monster": "https://www.monster.de/",
    "jobware": "https://www.jobware.de/",
    "kimeta": "https://www.kimeta.de/",
    "arbeitsagentur": "https://www.arbeitsagentur.de/jobsuche/",
}


def _find_chromium() -> str | None:
    """Return a real browser executable available inside the Render container."""
    candidates = []
    configured = os.getenv("CHROMIUM_PATH", "").strip()
    if configured:
        candidates.append(configured)

    candidates.extend([
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ])

    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


class BrowserAuthManager:
    def __init__(self):
        settings = get_settings()
        self.root = settings.data_dir / "browser_profiles"
        self.root.mkdir(parents=True, exist_ok=True)
        self.playwright = None
        self.contexts = {}
        self.pages = {}
        self.started = set()
        self.completed = set()
        self.lock = asyncio.Lock()

    def available(self):
        return async_playwright is not None

    def browser_info(self):
        executable = _find_chromium()
        bundled = None
        if async_playwright is not None:
            try:
                from playwright.async_api import async_playwright as _ap
                # The bundled executable is exposed without starting a browser.
                # It is only informational here; Docker installs it at build time.
                bundled = None
            except Exception:
                pass
        return {
            "playwright_installed": self.available(),
            "chromium_path": executable,
            "chromium_available": bool(executable),
            "display": os.getenv("DISPLAY", ""),
            "playwright_browsers_path": os.getenv("PLAYWRIGHT_BROWSERS_PATH", ""),
        }

    def state(self, source):
        info = self.browser_info()
        return {
            "source": source,
            "available": self.available() and info["chromium_available"],
            "started": source in self.started,
            "ready": source in self.completed,
            "login_url": LOGIN_URLS.get(source),
            "display": bool(os.getenv("DISPLAY")),
            "chromium_path": info["chromium_path"],
        }

    async def _ensure(self):
        if not self.available():
            raise RuntimeError("Playwright ist nicht installiert. Prüfe requirements.txt und den Render-Build.")
        executable = _find_chromium()
        if not executable:
            raise RuntimeError(
                "Chromium ist im Container nicht installiert. "
                "Der Render-Service muss mit dem mitgelieferten Dockerfile neu gebaut werden. "
                "Erwartet wird /usr/bin/chromium."
            )
        if not self.playwright:
            self.playwright = await async_playwright().start()
        return executable

    async def start_login(self, source):
        if source not in LOGIN_URLS:
            raise ValueError(f"Unsupported login source: {source}")
        async with self.lock:
            executable = await self._ensure()
            if source in self.contexts:
                page = self.pages.get(source)
                if page and not page.is_closed():
                    await page.bring_to_front()
                    return self.state(source)

            profile_dir = self.root / source
            profile_dir.mkdir(parents=True, exist_ok=True)

            # DISPLAY is created by start.sh. Therefore Render gets a real
            # headed Chromium that is exposed through the noVNC iframe.
            headless = not bool(os.getenv("DISPLAY"))
            log.info("Starting login browser source=%s executable=%s headless=%s", source, executable, headless)

            context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                viewport={"width": 1440, "height": 900},
                locale="de-DE",
                accept_downloads=False,
                executable_path=executable,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--window-size=1440,900",
                ],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(LOGIN_URLS[source], wait_until="domcontentloaded", timeout=30000)
            self.contexts[source] = context
            self.pages[source] = page
            self.started.add(source)
            return self.state(source)

    async def complete(self, source):
        if source not in self.contexts:
            await self.start_login(source)
        self.completed.add(source)
        return self.state(source)

    async def close(self):
        for context in list(self.contexts.values()):
            try:
                await context.close()
            except Exception:
                pass
        self.contexts.clear()
        self.pages.clear()
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    async def search(self, source, query, location, employment_type):
        if source not in self.completed or source not in self.contexts:
            return []
        page = self.pages[source]
        if page.is_closed():
            self.completed.discard(source)
            return []
        if source == "stepstone":
            url = f"https://www.stepstone.de/jobs/{quote_plus(query)}/in-{quote_plus(location)}"
        elif source == "indeed":
            url = f"https://de.indeed.com/jobs?q={quote_plus(query + ' ' + employment_type)}&l={quote_plus(location)}"
        else:
            return []
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1200)
            return await page.content()
        except Exception as exc:
            log.warning("Browser search failed for %s: %s", source, exc)
            return []
