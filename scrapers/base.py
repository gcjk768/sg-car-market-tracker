"""Shared scraper machinery: robots.txt checks, per domain throttling, daily page cache,
retry with backoff, and optional Playwright rendering for JavaScript heavy pages.

Every concrete scraper subclasses BaseScraper and implements parse().
"""
from __future__ import annotations

import hashlib
import logging
import random
import time
import urllib.robotparser
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Optional
from urllib.parse import urlsplit

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


class FetchError(Exception):
    """Raised when a page could not be fetched after retries."""


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

    def wait(self, domain: str) -> None:
        now = time.monotonic()
        last = self._last.get(domain)
        if last is not None:
            gap = self.seconds + random.uniform(0, self.jitter)
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
                if resp.status_code >= 400:
                    rp.parse([])
                else:
                    rp.parse(resp.text.splitlines())
            except httpx.HTTPError as exc:
                log.warning("could not read %s (%s), assuming allowed", robots_url, exc)
                rp.parse([])
            BaseScraper._robots[base] = rp
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
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
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

    def fetch_rendered(self, url: str, wait_selector: str | None = None, wait_ms: int = 2000) -> str:
        """Fetch a JavaScript rendered page with headless Chromium. Cached like fetch()."""
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        if not self._allowed(url):
            raise RobotsDisallowed(url)
        from playwright.sync_api import sync_playwright  # imported lazily, it is a heavy dependency

        BaseScraper._throttle.wait(urlsplit(url).netloc)
        log.info("RENDER %s", url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=self.user_agent)
                page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
                if wait_selector:
                    page.wait_for_selector(wait_selector, timeout=self.timeout * 1000)
                page.wait_for_timeout(wait_ms)
                html = page.content()
            finally:
                browser.close()
        self._write_cache(url, html)
        return html

    # Contract

    @abstractmethod
    def parse(self, html: str) -> list:
        """Turn one page of HTML into a list of pydantic models."""

    @abstractmethod
    def run(self) -> list:
        """Fetch everything this scraper needs and return the parsed models."""

    def close(self) -> None:
        self.client.close()
