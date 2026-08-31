import asyncio
import base64
import logging
import re
import time
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
import httpx
from bs4 import BeautifulSoup
from app.collectors.base import RawJob, safe_job_url, normalize_space
from app.services.job_parser import infer_employment_type

log = logging.getLogger(__name__)

SOURCE_DOMAINS = {
    "indeed": ("indeed.com",),
    "stepstone": ("stepstone.de",),
    "xing": ("xing.com",),
    "monster": ("monster.de", "monster.com"),
    "jobware": ("jobware.de",),
    "kimeta": ("kimeta.de",),
    "linkedin": ("linkedin.com",),
    "arbeitsagentur": ("arbeitsagentur.de",),
    "studis_online": ("studis-online.de",),
}

KNOWN_LISTING_PATHS = {
    ("studis-online.de", "/jobben/werkstudent.php"),
    ("studis-online.de", "/jobben/studentenjobs.php"),
}

NON_JOB_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".xml", ".txt", ".csv"}

GENERIC_LIST_PATHS = {
    "", "/", "/jobs", "/job", "/stellenangebote", "/stellenangebote/",
    "/karriere", "/career", "/careers", "/jobsuche", "/suche", "/search",
    "/stellenmarkt", "/jobboerse", "/jobsuche/stellenangebote",
}
DIRECT_PATH_HINTS = (
    "/job/", "/jobs/", "/stellenangebot", "/stellenangebote--", "/jobdetail/",
    "/jobs/view/", "/position/", "/vacancy/", "/requisition/", "/stellenanzeige/",
    "/jobposting/", "/job-detail/", "/career/job/", "/careers/job/", "/openings/",
    "/viewjob", "/pagead/clk", "/rc/clk",
)
JOB_QUERY_HINTS = ("jobid", "job_id", "vacancy", "vacancyid", "positionid", "requisitionid", "reqid", "jk")
JOB_TEXT_HINTS = (
    "werkstudent", "working student", "vollzeit", "full-time", "teilzeit", "part-time",
    "praktikum", "internship", "stellenangebot", "job description", "aufgaben", "qualifikationen",
    "bewerben", "apply now", "hiring organization", "wir suchen", "your responsibilities",
)
SEARCH_ENGINES = ("bing", "duckduckgo")


def _host_allowed(url: str, domains: tuple[str, ...]) -> bool:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        return any(host == d or host.endswith("." + d) for d in domains)
    except Exception:
        return False


def source_for_url(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    for source, domains in SOURCE_DOMAINS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return source
    return "generic"


def is_non_job_url(url: str) -> bool:
    """Reject documents/assets and obvious informational pages before fetching/parsing them."""
    if not safe_job_url(url):
        return True
    p = urlparse(url)
    path = p.path.lower().rstrip("/")
    if any(path.endswith(ext) for ext in NON_JOB_EXTENSIONS):
        return True
    host = p.netloc.lower()
    # Informational/legal/unrelated content on sites that are frequently returned
    # by broad job searches. These are never job detail pages.
    informational_tokens = (
        "/datenschutz", "/impressum", "/kontakt", "/ueber-uns", "/about",
        "/ratgeber", "/magazin", "/blog", "/news", "/wiki", "/download",
        "/uploads/", "/wp-content/uploads/", "/pdf/"
    )
    return any(token in path for token in informational_tokens)


def is_direct_job_url(url: str, source: str) -> bool:
    if is_non_job_url(url):
        return False
    p = urlparse(url)
    path = p.path.lower().rstrip("/")
    query = p.query.lower()
    if source != "generic" and not _host_allowed(url, SOURCE_DOMAINS.get(source, ())):
        return False
    if path in GENERIC_LIST_PATHS:
        return False
    if source == "indeed":
        return ("/viewjob" in path or "/pagead/clk" in path or "/rc/clk" in path or "jk=" in query) and "jobs?q=" not in query
    if source == "stepstone":
        return bool(re.search(r"/stellenangebote--[^/?#]+", path) or re.search(r"/jobs?/[^/?#]+", path) or "/stellenangebot/" in path or "/stellenangebote/" in path)
    if source == "linkedin":
        return bool(re.search(r"/jobs/view/[^/?#]+", path))
    if source == "xing":
        return bool(re.search(r"/jobs/[^/?#]+", path))
    if source == "arbeitsagentur":
        return "/jobdetail/" in path
    if source == "studis_online":
        # The /jobben/ area contains many editorial/info pages (werkstudent.php,
        # minijob.php, einkommensgrenzen.php, ...). Treat only extensionless job
        # slugs or explicit job-query URLs as possible internal detail pages.
        if p.netloc.lower().split(":")[0].endswith("studis-online.de"):
            if path in {"/jobben/werkstudent.php", "/jobben/studentenjobs.php"}:
                return False
            if not path.startswith("/jobben/"):
                return False
            if path.endswith(".php") or path.endswith(".html"):
                return False
            segments = [x for x in path.split("/") if x]
            return len(segments) >= 2 and any(
                token in path for token in (
                    "werkstudent", "student", "praktik", "intern",
                    "analyst", "developer", "engineer", "consult", "marketing",
                    "finance", "controlling", "it-", "data", "business"
                )
            )
        # External ATS/employer links found on Studis listings must themselves
        # look like job-detail URLs.
        return is_generic_job_url(url)
    if source in {"monster", "jobware", "kimeta"}:
        return any(x in path for x in DIRECT_PATH_HINTS) or any(k in query for k in JOB_QUERY_HINTS)
    return is_generic_job_url(url)


def is_generic_job_url(url: str) -> bool:
    if is_non_job_url(url):
        return False
    p = urlparse(url)
    host = p.netloc.lower()
    if any(x in host for x in ("duckduckgo.", "bing.", "google.", "yahoo.")):
        return False
    path = p.path.lower().rstrip("/")
    if path in GENERIC_LIST_PATHS:
        return False
    if any(token in path for token in DIRECT_PATH_HINTS):
        return True
    if any(k in p.query.lower() for k in JOB_QUERY_HINTS):
        return True
    segments = [s for s in path.split("/") if s]
    return len(segments) >= 2 and any(x in path for x in ("career", "careers", "jobs", "job", "position", "vacanc", "opening", "stellen"))


class PublicDiscovery:
    """Public search discovery with polite throttling, retries and provider isolation.

    This intentionally does not attempt to defeat anti-bot controls. A blocked provider is
    marked unavailable and the next provider is used instead.
    """
    def __init__(self, client: httpx.AsyncClient, max_results=10, min_interval=0.75,
                 request_timeout=8.0, preferred_provider: str | None = None):
        self.client = client
        self.max_results = max(1, int(max_results))
        self.min_interval = max(0.2, float(min_interval))
        self.request_timeout = max(2.0, float(request_timeout))
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0
        self.last_provider = None
        self.last_error = None
        preferred = (preferred_provider or "bing").strip().lower()
        self.providers = [preferred] + [p for p in SEARCH_ENGINES if p != preferred]
        self.provider_state = {p: "ready" for p in SEARCH_ENGINES}

    async def _throttle(self):
        async with self._rate_lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _get(self, url: str) -> httpx.Response:
        await self._throttle()
        return await self.client.get(url, timeout=self.request_timeout, follow_redirects=True)

    @staticmethod
    def _unwrap(url: str) -> str:
        try:
            current = unquote(url)
            p = urlparse(current)
            qs = parse_qs(p.query)
            for key in ("uddg", "url", "target", "dest", "destination"):
                value = qs.get(key, [None])[0]
                if value:
                    value = unquote(value)
                    if urlparse(value).scheme in {"http", "https"}:
                        return value
            value = qs.get("u", [None])[0]
            if value:
                candidates = [value[2:]] if value.startswith("a1") else []
                candidates.append(value)
                for encoded in candidates:
                    try:
                        padded = encoded + "=" * (-len(encoded) % 4)
                        decoded = base64.urlsafe_b64decode(padded).decode()
                        if urlparse(decoded).scheme in {"http", "https"}:
                            return decoded
                    except Exception:
                        pass
                if urlparse(value).scheme in {"http", "https"}:
                    return value
        except Exception:
            pass
        return url

    @staticmethod
    def _candidate(title: str, href: str, snippet: str, source: str) -> RawJob | None:
        href = PublicDiscovery._unwrap(href)
        title = normalize_space(title)
        snippet = normalize_space(snippet)
        if not title or len(title) < 5 or not safe_job_url(href):
            return None
        if source != "generic":
            if not _host_allowed(href, SOURCE_DOMAINS.get(source, ())):
                return None
            if urlparse(href).path.lower().rstrip("/") in GENERIC_LIST_PATHS:
                return None
            direct = is_direct_job_url(href, source)
            # Search engines sometimes expose a valid job page using a new URL pattern.
            # Keep it as a candidate when the result itself strongly looks like a job;
            # enrichment performs the final validation.
            if not direct and not any(x in (title + " " + snippet).lower() for x in JOB_TEXT_HINTS):
                return None
        else:
            # Generic discovery may return a known job-listing page. Keep it as an
            # expansion candidate, never as a final job. The scanner will extract
            # concrete detail links from it before parsing/matching.
            hp = urlparse(href)
            hpath = hp.path.lower().rstrip("/")
            hhost = hp.netloc.lower().split(":")[0]
            is_known_listing = (hhost.endswith("studis-online.de") and hpath in {"/jobben/werkstudent.php", "/jobben/studentenjobs.php"})
            if not is_generic_job_url(href) and not is_known_listing:
                return None
        # Search-engine snippets are a legitimate fallback when a portal blocks
        # direct enrichment.  Keep only concrete, source-domain URLs; never turn
        # a portal homepage/search page into a job.
        generic_title_prefixes = ("jobs in ", "stellenangebote in ", "jobsuche", "job search", "stellenmarkt", "karriere", "careers")
        if any(title.lower().startswith(prefix) for prefix in generic_title_prefixes):
            return None
        company = ""
        location = ""
        parts = [normalize_space(x) for x in re.split(r"\s*[·|]\s*", snippet) if normalize_space(x)]
        if parts:
            for part in parts:
                if not location and re.search(r"\b\d{5}\b|\bberlin\b|\bpotsdam\b|\bhamburg\b|\bmünchen\b|\bfrankfurt\b", part, re.I):
                    location = part[:300]
                    continue
                if not company and len(part) <= 160 and not any(re.search(p, part, re.I) for p in JOB_TEXT_HINTS):
                    company = part[:300]
        return RawJob(
            title=title[:500], company=company, location=location,
            description=snippet[:4000], url=href,
            source=source, employment_type=infer_employment_type(" ".join((title, snippet)))
        )

    @staticmethod
    def _parse_bing(html: str, source: str, max_results: int) -> list[RawJob]:
        soup = BeautifulSoup(html or "", "html.parser")
        out, seen = [], set()
        anchors = []
        for item in soup.select("li.b_algo"):
            a = item.select_one("h2 a") or item.select_one("a")
            if a:
                anchors.append((a, item))
        if not anchors:
            for a in soup.select("main a[href], #b_content a[href], h2 a[href]"):
                anchors.append((a, a.parent))
        for a, container in anchors:
            href = a.get("href", "")
            if not href:
                continue
            snippet_node = None
            if container:
                snippet_node = container.select_one(".b_caption p, .b_paractl, p")
            raw = PublicDiscovery._candidate(
                a.get_text(" ", strip=True), href,
                snippet_node.get_text(" ", strip=True) if snippet_node else "", source
            )
            if raw and raw.url not in seen:
                seen.add(raw.url); out.append(raw)
            if len(out) >= max_results:
                break
        return out

    @staticmethod
    def _parse_ddg(html: str, source: str, max_results: int) -> list[RawJob]:
        soup = BeautifulSoup(html or "", "html.parser")
        out, seen = [], set()
        for a in soup.select("a.result__a, a.result-link"):
            container = a.find_parent(class_="result") or a.parent
            snippet_node = container.select_one(".result__snippet") if container else None
            raw = PublicDiscovery._candidate(
                a.get_text(" ", strip=True), a.get("href", ""),
                snippet_node.get_text(" ", strip=True) if snippet_node else "", source
            )
            if raw and raw.url not in seen:
                seen.add(raw.url); out.append(raw)
            if len(out) >= max_results:
                break
        return out

    async def _engine(self, q: str, source: str, provider: str) -> list[RawJob]:
        if provider == "bing":
            r = await self._get("https://www.bing.com/search?q=" + quote_plus(q) + "&count=" + str(max(self.max_results, 10)))
            if r.status_code in (403, 429):
                self.provider_state[provider] = f"blocked:{r.status_code}"
                raise RuntimeError(f"Bing HTTP {r.status_code}")
            r.raise_for_status()
            rows = self._parse_bing(r.text, source, self.max_results)
            if not rows:
                raise RuntimeError("Bing: keine verwertbaren Kandidaten")
            return rows
        last = None
        for endpoint in ("https://html.duckduckgo.com/html/?q=", "https://lite.duckduckgo.com/lite/?q="):
            try:
                r = await self._get(endpoint + quote_plus(q))
                if r.status_code in (403, 429):
                    self.provider_state[provider] = f"blocked:{r.status_code}"
                    last = RuntimeError(f"DuckDuckGo HTTP {r.status_code}")
                    continue
                r.raise_for_status()
                rows = self._parse_ddg(r.text, source, self.max_results)
                if rows:
                    return rows
                last = RuntimeError("DuckDuckGo: keine verwertbaren Kandidaten")
            except Exception as exc:
                last = exc
        raise last or RuntimeError("DuckDuckGo unavailable")

    async def search(self, q: str, source: str) -> list[RawJob]:
        errors = []
        for provider in self.providers:
            try:
                rows = await self._engine(q, source, provider)
                if rows:
                    self.last_provider = provider
                    self.last_error = None
                    self.provider_state[provider] = "ok"
                    return rows
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
                log.debug("discovery provider %s failed: %s", provider, exc)
        self.last_provider = None
        self.last_error = " | ".join(errors) or "keine Discovery-Provider verfügbar"
        raise RuntimeError(self.last_error)

    async def search_site(self, query: str, location: str, source: str, employment_type: str) -> list[RawJob]:
        """Find concrete public detail URLs without bypassing access controls."""
        domain = SOURCE_DOMAINS[source][0]
        variants = [
            f'site:{domain} "{employment_type}" "{query}" "{location}"',
            f'site:{domain} "{employment_type}" "{query}" "{location}" (job OR stellenangebot OR stellenangebote)',
        ]
        if source == "indeed":
            variants.insert(0, f'site:{domain}/viewjob "{employment_type}" "{query}" "{location}"')
        elif source == "stepstone":
            variants.insert(0, f'site:{domain}/stellenangebote-- "{employment_type}" "{query}" "{location}"')
        out, seen = [], set()
        for q in variants:
            try:
                rows = await self.search(q, source)
            except Exception as exc:
                log.debug("%s discovery query failed: %s", source, exc)
                continue
            for row in rows:
                key = row.url.split("#", 1)[0].rstrip("/")
                if key in seen or not is_direct_job_url(row.url, source):
                    continue
                seen.add(key)
                out.append(row)
                if len(out) >= self.max_results:
                    return out
        if not out:
            raise RuntimeError(f"{source}: keine konkreten Stellenanzeigen gefunden")
        return out

    async def search_generic(self, query: str, location: str, employment_type: str) -> list[RawJob]:
        q = f'"{employment_type}" "{query}" "{location}" (job OR jobs OR stellenangebot OR stellenangebote OR karriere OR career) -jobsuche -stellenmarkt'
        return await self.search(q, "generic")
