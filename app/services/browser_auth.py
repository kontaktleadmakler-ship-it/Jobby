import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import quote_plus, urlparse, urljoin

from app.config import get_settings
from app.collectors.base import RawJob, normalize_space

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

    def state(self, source):
        return {
            "source": source,
            "available": self.available(),
            "started": source in self.started,
            "ready": source in self.completed,
            "login_url": LOGIN_URLS.get(source),
            "display": bool(os.getenv("DISPLAY")),
        }

    async def _ensure(self):
        if not self.available():
            raise RuntimeError("Playwright is not installed")
        if not self.playwright:
            self.playwright = await async_playwright().start()

    async def start_login(self, source):
        if source not in LOGIN_URLS:
            raise ValueError(f"Unsupported login source: {source}")
        async with self.lock:
            await self._ensure()
            if source in self.contexts:
                page = self.pages.get(source)
                if page and not page.is_closed():
                    await page.bring_to_front()
                    return self.state(source)
            profile_dir = self.root / source
            profile_dir.mkdir(parents=True, exist_ok=True)
            headless = not bool(os.getenv("DISPLAY"))
            # Headed mode is intentional: the user must perform the login/2FA/CAPTCHA themselves.
            launch_kwargs = {
                "user_data_dir": str(profile_dir),
                "headless": headless,
                "viewport": {"width": 1440, "height": 900},
                "locale": "de-DE",
                "accept_downloads": False,
            }
            executable = os.getenv("CHROMIUM_PATH", "").strip()
            if executable and Path(executable).exists():
                launch_kwargs["executable_path"] = executable
            context = await self.playwright.chromium.launch_persistent_context(**launch_kwargs)
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

    async def search_candidates(self, source, query, location, employment_type, limit=50):
        html = await self.search(source, query, location, employment_type)
        if not html:
            return []
        # Import lazily to avoid the discovery/browser-auth import cycle.
        from app.services.discovery import SOURCE_DOMAINS, is_direct_job_url, is_non_job_url
        from bs4 import BeautifulSoup

        base_url = (
            f"https://www.stepstone.de/jobs/{quote_plus(query)}/in-{quote_plus(location)}"
            if source == "stepstone"
            else f"https://de.indeed.com/jobs?q={quote_plus(query + ' ' + employment_type)}&l={quote_plus(location)}"
        )
        soup = BeautifulSoup(html, "html.parser")
        out, seen = [], set()
        base_host = urlparse(base_url).netloc.lower().split(":")[0]
        for a in soup.select("a[href]"):
            href = urljoin(base_url, a.get("href", "").strip())
            title = normalize_space(a.get_text(" ", strip=True))
            if not href or len(title) < 8:
                continue
            host = urlparse(href).netloc.lower().split(":")[0]
            if not any(host == d or host.endswith("." + d) for d in SOURCE_DOMAINS[source]):
                continue
            if is_non_job_url(href) or not is_direct_job_url(href, source):
                continue
            key = href.split("#", 1)[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            container = a
            for parent in range(3):
                if container.parent:
                    container = container.parent
            snippet = normalize_space(container.get_text(" ", strip=True))
            out.append(RawJob(
                title=title[:500],
                description=snippet[:4000],
                url=href,
                source=source,
                employment_type=employment_type,
            ))
            if len(out) >= limit:
                break
        return out

    async def close(self):
        for context in list(self.contexts.values()):
            try:
                await context.close()
            except Exception:
                pass
        self.contexts.clear(); self.pages.clear()
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
