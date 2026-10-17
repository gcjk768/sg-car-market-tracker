"""Shared scraper machinery: robots.txt checks, per domain throttling, daily page cache,
retry with backoff, and optional Playwright rendering for JavaScript heavy pages.

Every concrete scraper subclasses BaseScraper and implements parse().
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import tempfile
import time
import urllib.robotparser
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Optional
from urllib.parse import urlsplit

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


BLOCK_MARKERS = ("just a moment", "verify you are human", "prove you are human", "captcha", "access denied",
                 "attention required")


def _transient(exc: BaseException, retry_429: bool = True) -> bool:
    """Worth retrying: network errors, rate limits and server errors. A 404 is not, and neither
    is a 403: that is an anti bot block (Tesla), and asking again would be trying to get past it."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return (code == 429 and retry_429) or code >= 500
    return isinstance(exc, httpx.TransportError)


def _status_error(url: str, status: int) -> httpx.HTTPStatusError:
    """The error a plain GET raises for this status, for an answer the browser got."""
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(f"{status} for {url}", request=req, response=httpx.Response(status, request=req))


class FetchError(Exception):
    """Raised when a page could not be fetched after retries."""


class HttpBlocked(FetchError):
    """The browser got a 403 or 429: the site said no, and it is never retried around."""


class RobotsDisallowed(Exception):
    """Raised when robots.txt forbids the requested path for our user agent."""


class ScraperUnavailable(Exception):
    """Raised by a scraper when its section cannot be produced today."""


class _Throttle:
    """One request per domain every throttle_seconds plus random jitter."""

    def __init__(self, seconds: float, jitter: float):
        self.seconds = seconds
        self.jitter = jitter
        self._last: dict[str, float] = {}
        # Crawl-delay from each site's robots.txt, used when longer than our own gap.
        self.min_gap: dict[str, float] = {}

    def wait(self, domain: str) -> None:
        now = time.monotonic()
        last = self._last.get(domain)
        if last is not None:
            gap = max(self.seconds, self.min_gap.get(domain, 0.0)) + random.uniform(0, self.jitter)
            remaining = last + gap - now
            if remaining > 0:
                time.sleep(remaining)
        self._last[domain] = time.monotonic()


class BaseScraper(ABC):
    """Base class for all scrapers.

    Subclasses set `name` and implement `parse`, which returns pydantic models. Use `fetch`
    for static pages and `fetch_rendered` for pages that need JavaScript.
    """

    name: ClassVar[str] = "base"
    _throttle: ClassVar[Optional[_Throttle]] = None
    _robots: ClassVar[dict[str, urllib.robotparser.RobotFileParser]] = {}
    # Hosts whose robots.txt plain requests could not read (timeout, 403, 5xx): read through the browser before use.
    _robots_unread: ClassVar[set[str]] = set()
    retry_429: ClassVar[bool] = True   # False: a 429 is final and never asked again

    def __init__(self, cfg: dict[str, Any], user_agent: str, run_date: date | None = None, force: bool = False):
        self.cfg = cfg
        general = cfg["general"]
        self.user_agent = user_agent
        self.run_date = run_date or date.today()
        self.force = force
        self.timeout = general.get("request_timeout_seconds", 30)
        self.max_retries = general.get("max_retries", 3)
        self.honour_robots = general.get("honour_robots_txt", True)
        self.cache_root = Path(general.get("cache_dir", "data/cache")) / self.run_date.isoformat()
        if BaseScraper._throttle is None:
            BaseScraper._throttle = _Throttle(
                general.get("throttle_seconds", 2.0), general.get("throttle_jitter_seconds", 1.5)
            )
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "en-SG,en;q=0.9"},
            timeout=self.timeout,
            follow_redirects=True,
        )

    # Cache

    def _cache_path(self, url: str, suffix: str = ".html") -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
        host = urlsplit(url).netloc.replace(":", "_")
        return self.cache_root / host / f"{digest}{suffix}"

    def _read_cache(self, url: str) -> Optional[str]:
        if self.force:
            return None
        path = self._cache_path(url)
        if path.exists():
            log.debug("cache hit %s", url)
            return path.read_text(encoding="utf-8")
        return None

    def _write_cache(self, url: str, text: str) -> None:
        path = self._cache_path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    # Robots

    def _allowed(self, url: str) -> bool:
        if not self.honour_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = BaseScraper._robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f"{base}/robots.txt"
            try:
                BaseScraper._throttle.wait(parts.netloc)
                resp = self.client.get(robots_url)
                if resp.status_code == 403 or resp.status_code >= 500:
                    BaseScraper._robots_unread.add(base)
                rp.parse([] if resp.status_code >= 400 else resp.text.splitlines())
            except httpx.HTTPError as exc:
                log.warning("could not read %s (%s), assuming allowed for plain requests", robots_url, exc)
                rp.parse([])
                BaseScraper._robots_unread.add(base)
            self._keep_robots(base, rp)
        return rp.can_fetch(self.user_agent, url)

    def _keep_robots(self, base: str, rp: urllib.robotparser.RobotFileParser) -> None:
        BaseScraper._robots[base] = rp
        delay = rp.crawl_delay(self.user_agent)
        if delay:
            BaseScraper._throttle.min_gap[urlsplit(base).netloc] = float(delay)

    def _allowed_in_browser(self, url: str) -> bool:
        """_allowed for a read through the browser. A robots.txt that plain requests could not read is read through
        the browser first, and a host whose robots.txt cannot be read either way is not read at all."""
        if not self._allowed(url):
            return False
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in BaseScraper._robots_unread:
            return True
        robots_url = f"{base}/robots.txt"
        BaseScraper._throttle.wait(parts.netloc)
        log.info("RENDER %s", robots_url)
        limit = self.cfg["general"].get("render_timeout_seconds", self.timeout) * 1000
        try:
            try:
                with self._browser_page() as page:
                    resp = page.goto(robots_url, timeout=limit)
                    if resp is None or resp.status == 403 or resp.status >= 429:
                        raise FetchError(f"{resp.status if resp else 'no answer'} for {robots_url}")
                    lines = resp.text().splitlines() if resp.status < 400 else []
            except Exception as exc:
                if "Download is starting" not in str(exc):
                    raise
                # some hosts (BMW) send robots.txt as a file, which Chromium downloads instead of showing
                lines = self.fetch_download(robots_url).decode("utf-8", "replace").splitlines()
        except Exception as exc:
            log.warning("could not read %s through the browser either (%s), not reading %s", robots_url, exc, url)
            return False
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(lines)
        self._keep_robots(base, rp)
        BaseScraper._robots_unread.discard(base)
        return rp.can_fetch(self.user_agent, url)

    # Fetching

    def fetch(self, url: str, params: dict[str, Any] | None = None) -> str:
        """Return page text, from the daily cache when available."""
        if params:
            url = str(httpx.URL(url, params=params))
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        if not self._allowed(url):
            raise RobotsDisallowed(url)
        text = self._get_with_retry(url)
        self._write_cache(url, text)
        return text

    def _get_with_retry(self, url: str) -> str:
        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception(lambda e: _transient(e, self.retry_429)),
            reraise=True,
        )
        def _go() -> str:
            BaseScraper._throttle.wait(urlsplit(url).netloc)
            log.info("GET %s", url)
            resp = self.client.get(url)
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} for {url}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.text

        try:
            return _go()
        except httpx.HTTPError as exc:
            raise FetchError(f"{url}: {exc}") from exc

    @contextmanager
    def _browser_page(self):
        """A page in headless Chromium with the app's user agent, images, media and fonts not downloaded.

        The shared Playwright server on the NAS (PLAYWRIGHT_WS_URL, reachable on Docker network scrape-net) is used
        when set, per the owner's rule of 2026-10-08. If it cannot be reached, a local Chromium is launched instead."""
        from playwright.sync_api import sync_playwright  # imported lazily, it is a heavy dependency

        with sync_playwright() as p:
            browser = None
            ws = os.getenv("PLAYWRIGHT_WS_URL")
            if ws:
                try:
                    browser = p.chromium.connect(ws, timeout=30000)
                except Exception as exc:
                    log.warning("shared Playwright server %s unreachable (%s), launching a local Chromium", ws, exc)
            if browser is None:
                launch_kwargs = {"headless": True}
                executable = os.getenv("CHROMIUM_EXECUTABLE_PATH")
                if executable:
                    launch_kwargs["executable_path"] = executable
                browser = p.chromium.launch(**launch_kwargs)
            try:
                page = browser.new_page(user_agent=self.user_agent)
                # Only the HTML is read. Images, fonts and media made Sgcarmart's results page miss the
                # 30 second load limit on the NAS, so they are not downloaded at all.
                page.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media", "font")
                           else route.continue_())
                yield page
            finally:
                browser.close()

    @staticmethod
    def _check_status(resp, url: str) -> None:
        if resp is not None and resp.status in (403, 429):
            raise HttpBlocked(f"{resp.status} for {url}")

    def fetch_rendered(self, url: str, wait_selector: str | None = None, wait_ms: int = 2000,
                       check_robots: bool = True) -> str:
        """Fetch a JavaScript rendered page with headless Chromium. Cached like fetch(). check_robots=False only for
        a site the owner named in config (official.playwright_robots_override)."""
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        if check_robots and not self._allowed_in_browser(url):
            raise RobotsDisallowed(url)
        BaseScraper._throttle.wait(urlsplit(url).netloc)
        log.info("RENDER %s", url)
        limit = self.cfg["general"].get("render_timeout_seconds", self.timeout) * 1000
        with self._browser_page() as page:
            resp = page.goto(url, timeout=limit, wait_until="domcontentloaded")
            self._check_status(resp, url)
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=limit)
            page.wait_for_timeout(wait_ms)
            html = page.content()
        self._write_cache(url, html)
        return html

    def fetch_download(self, url: str) -> bytes:
        """Bytes of a file (a PDF) through headless Chromium's own network stack, for a site that refuses or stalls
        plain requests. Headless Chromium downloads a PDF instead of showing it. Not cached: callers cache the text.
        Robots and the throttle are the caller's job, as for a plain GET. A page instead of a file raises at once:
        HttpBlocked for a 403, a 429 or a challenge page, else FetchError (caused by an httpx error for a 4xx or 5xx)."""
        log.info("DOWNLOAD %s", url)
        limit = self.cfg["general"].get("render_timeout_seconds", self.timeout) * 1000
        with self._browser_page() as page:
            with page.expect_download(timeout=limit) as info:
                try:
                    resp = page.goto(url, timeout=limit)
                except Exception as exc:   # Chromium aborts the navigation once the download starts
                    if "Download is starting" not in str(exc):
                        raise
                else:   # the navigation finished, so a page came back and no file is coming
                    self._check_status(resp, url)
                    if any(m in page.content()[:5000].lower() for m in BLOCK_MARKERS):
                        raise HttpBlocked(f"challenge page for {url}")
                    status = resp.status if resp is not None else 0
                    raise FetchError(f"{url}: {status}, a page and no file") from (
                        _status_error(url, status) if status >= 400 else None)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "file"
                info.value.save_as(path)   # save_as, not path(): path() is unavailable on a remote browser
                return path.read_bytes()

    # Contract

    @abstractmethod
    def parse(self, html: str) -> list:
        """Turn one page of HTML into a list of pydantic models."""

    @abstractmethod
    def run(self) -> list:
        """Fetch everything this scraper needs and return the parsed models."""

    def close(self) -> None:
        self.client.close()
