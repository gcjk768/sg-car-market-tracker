"""The browser fallback: the shared Playwright server (owner's rule, 2026-10-08) and when page() and pdf() use it."""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scrapers.base import BaseScraper, FetchError, HttpBlocked, RobotsDisallowed
from scrapers.official import Blocked, OfficialScraper, mazda


# The shared Playwright server


class FakePage:
    def __init__(self, status=200, download=b"", html="<html>drawn</html>"):
        self.status, self.download, self.html = status, download, html
        self.robots = None   # the robots.txt the browser reads; None answers 403
        self.robots_file = None   # robots.txt sent as a file download instead (BMW)
        self.visited = []

    def route(self, *a):
        pass

    def goto(self, url, **k):
        self.visited.append(url)
        if url.endswith("/robots.txt") and self.robots_file is not None:
            raise Exception("Page.goto: Download is starting")
        if url.endswith("/robots.txt"):
            return SimpleNamespace(status=403 if self.robots is None else 200, text=lambda: self.robots)
        if self.download:
            raise Exception("Page.goto: Download is starting")
        return SimpleNamespace(status=self.status)

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return self.html

    @contextmanager
    def expect_download(self, timeout=None):
        data = self.download or self.robots_file
        yield SimpleNamespace(value=SimpleNamespace(save_as=lambda path: Path(path).write_bytes(data)))


@pytest.fixture
def chromium(monkeypatch):
    """A fake playwright: records connect and launch calls."""
    calls, page = [], FakePage()

    class Browser:
        def new_page(self, user_agent):
            calls.append(("ua", user_agent))
            return chromium.page

        def close(self):
            calls.append(("close",))

    class Chromium:
        connect_fails = False

        def connect(self, ws, timeout=None):
            calls.append(("connect", ws))
            if self.connect_fails:
                raise Exception("connection refused")
            return Browser()

        def launch(self, **kw):
            calls.append(("launch",))
            return Browser()

    chromium = Chromium()
    chromium.calls, chromium.page = calls, page

    @contextmanager
    def sync_playwright():
        yield SimpleNamespace(chromium=chromium)

    monkeypatch.setattr("playwright.sync_api.sync_playwright", sync_playwright)
    monkeypatch.setattr(BaseScraper, "_robots", {})
    monkeypatch.setattr(BaseScraper, "_robots_unread", set())
    return chromium


@pytest.fixture
def scraper(cfg, tmp_path, monkeypatch):
    cfg["general"]["cache_dir"] = str(tmp_path)
    cfg["general"]["max_retries"] = 1
    s = OfficialScraper(cfg, "honest-ua", date(2026, 10, 17))
    monkeypatch.setattr(BaseScraper._throttle, "wait", lambda d: None)
    monkeypatch.setattr(s, "_allowed", lambda u: True)
    return s


def test_rendered_page_uses_the_shared_server(chromium, scraper, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_WS_URL", "ws://playwright:3000/")
    assert scraper.fetch_rendered("https://a.example/p") == "<html>drawn</html>"
    assert ("connect", "ws://playwright:3000/") in chromium.calls and ("launch",) not in chromium.calls
    assert ("ua", "honest-ua") in chromium.calls and ("close",) in chromium.calls
    chromium.calls.clear()
    assert scraper.fetch_rendered("https://a.example/p") == "<html>drawn</html>" and chromium.calls == []   # day cache


def test_unreachable_server_falls_back_to_a_local_chromium(chromium, scraper, monkeypatch, caplog):
    monkeypatch.setenv("PLAYWRIGHT_WS_URL", "ws://playwright:3000/")
    chromium.connect_fails = True
    assert scraper.fetch_rendered("https://b.example/p") == "<html>drawn</html>"
    assert ("launch",) in chromium.calls and "unreachable" in caplog.text


def test_no_server_set_launches_locally(chromium, scraper, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_WS_URL", raising=False)
    scraper.fetch_rendered("https://c.example/p")
    assert [c[0] for c in chromium.calls if c[0] in ("connect", "launch")] == ["launch"]


@pytest.mark.parametrize("status", [403, 429])
def test_browser_refusal_is_blocked_and_not_cached(chromium, scraper, status):
    chromium.page.status = status
    with pytest.raises(HttpBlocked):
        scraper.fetch_rendered("https://d.example/p")
    with pytest.raises(Blocked):
        scraper.rendered("https://d.example/p")
    assert not scraper._cache_path("https://d.example/p").exists()


def test_download_reads_the_file_through_the_browser(chromium, scraper):
    chromium.page.download = b"%PDF-1.7 bytes"
    assert scraper.fetch_download("https://e.example/list.pdf") == b"%PDF-1.7 bytes"


@pytest.mark.parametrize("status,html,code", [(404, "<html>Not found</html>", 404), (500, "<html>Oops</html>", 500),
                                              (200, "<html>Our price list</html>", None)])
def test_download_of_a_page_instead_of_a_file_fails_at_once(chromium, scraper, status, html, code):
    chromium.page.status, chromium.page.html = status, html
    with pytest.raises(FetchError) as err:
        scraper.fetch_download("https://e.example/gone.pdf")
    assert not isinstance(err.value, HttpBlocked)
    cause = err.value.__cause__
    assert (cause.response.status_code if cause is not None else None) == code


def test_download_of_a_challenge_page_marks_the_brand_blocked(chromium, scraper, monkeypatch):
    chromium.page.html = "<html><title>Just a moment...</title>Verify you are human</html>"
    with pytest.raises(HttpBlocked):
        scraper.fetch_download("https://e.example/list.pdf")
    monkeypatch.setattr(scraper, "_allowed", lambda u: False)   # Mazda's /Upload list goes straight to the browser
    scraper.brand = "Mazda"
    with pytest.raises(Blocked):
        scraper.pdf_bytes("https://e.example/Upload/list.pdf")


def test_mazda_reads_a_browser_404_as_a_missing_list(chromium, scraper, monkeypatch):
    chromium.page.status, chromium.page.html = 404, "<html>Not found</html>"
    monkeypatch.setattr(scraper, "_allowed", lambda u: False)
    scraper.brand = "Mazda"
    assert mazda._pdf_runs(scraper, "https://e.example/Upload/old.pdf") == ""


def test_robots_txt_plain_requests_cannot_read_is_read_through_the_browser(chromium, scraper, monkeypatch):
    monkeypatch.setattr(scraper, "_allowed", OfficialScraper._allowed.__get__(scraper))

    def stall(request):   # BMW and Mercedes stall every plain request, robots.txt included
        raise httpx.ReadTimeout("timed out", request=request)

    _client(scraper, stall)
    chromium.page.robots = "User-agent: *\nDisallow: /private/\n"
    with pytest.raises(RobotsDisallowed):
        scraper.page("https://bmw.example/private/")
    assert chromium.page.visited == ["https://bmw.example/robots.txt"]
    assert scraper.page("https://bmw.example/models/") == "<html>drawn</html>"
    with pytest.raises(RobotsDisallowed):
        scraper.pdf_bytes("https://two.example/private/list.pdf")
    assert chromium.page.visited == ["https://bmw.example/robots.txt", "https://bmw.example/models/",
                                     "https://two.example/robots.txt"]
    chromium.page.robots = None   # robots.txt refused in the browser too: the site is not read at all
    with pytest.raises(RobotsDisallowed):
        scraper.page("https://three.example/models/")
    assert chromium.page.visited[-1] == "https://three.example/robots.txt"


def test_robots_txt_sent_as_a_download_is_read_from_the_file(chromium, scraper, monkeypatch):
    """BMW's robots.txt makes Chromium start a download; the site was skipped as unreadable on 17 Oct."""
    monkeypatch.setattr(scraper, "_allowed", OfficialScraper._allowed.__get__(scraper))
    _client(scraper, lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timed out", request=request)))
    chromium.page.robots_file = b"User-agent: *\nDisallow: /private/\n"
    assert scraper._allowed_in_browser("https://bmw.example/models/")
    assert not scraper._allowed_in_browser("https://bmw.example/private/x")


# When page() and pdf() fall back


def _client(scraper, handler):
    scraper.client = httpx.Client(transport=httpx.MockTransport(handler))


def _status(code):
    return lambda request: httpx.Response(code, text="no")


def _spy(scraper, monkeypatch):
    seen = []
    monkeypatch.setattr(scraper, "fetch_rendered", lambda url, check_robots=True, **k: seen.append((url, check_robots)) or "<p>drawn</p>")
    monkeypatch.setattr(scraper, "fetch_download", lambda url: seen.append((url, "download")) or b"%PDF")
    return seen


def test_page_403_retries_through_the_browser(scraper, monkeypatch):
    _client(scraper, _status(403))
    seen = _spy(scraper, monkeypatch)
    assert scraper.page("https://f.example/") == "<p>drawn</p>" and seen == [("https://f.example/", True)]


def test_page_timeout_retries_through_the_browser(scraper, monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    _client(scraper, slow)
    seen = _spy(scraper, monkeypatch)
    assert scraper.page("https://g.example/") == "<p>drawn</p>" and seen


def test_page_script_wall_retries_through_the_browser(scraper, monkeypatch):
    _client(scraper, lambda r: httpx.Response(200, text="<noscript>Please enable JavaScript to view this site</noscript>"))
    seen = _spy(scraper, monkeypatch)
    assert scraper.page("https://h.example/") == "<p>drawn</p>" and seen


def test_page_challenge_that_also_asks_for_javascript_is_blocked(scraper, monkeypatch):
    _client(scraper, lambda r: httpx.Response(200, text="<title>Just a moment...</title> Enable JavaScript and cookies "
                                                         "to continue. Verify you are human"))
    seen = _spy(scraper, monkeypatch)
    with pytest.raises(Blocked):
        scraper.page("https://h2.example/")
    assert seen == []


def test_page_429_is_blocked_after_one_request(scraper, monkeypatch):
    scraper.max_retries = 3
    calls = []
    _client(scraper, lambda r: calls.append(r) or httpx.Response(429, text="slow down"))
    seen = _spy(scraper, monkeypatch)
    with pytest.raises(Blocked):
        scraper.page("https://i.example/")
    assert seen == [] and len(calls) == 1


def test_pdf_403_and_timeout_download_through_the_browser_429_blocks(scraper, monkeypatch):
    seen = _spy(scraper, monkeypatch)
    _client(scraper, _status(403))
    assert scraper.pdf_bytes("https://j.example/a.pdf") == b"%PDF"

    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _client(scraper, slow)
    assert scraper.pdf_bytes("https://j.example/b.pdf") == b"%PDF"
    assert seen == [("https://j.example/a.pdf", "download"), ("https://j.example/b.pdf", "download")]
    _client(scraper, _status(429))
    with pytest.raises(Blocked):
        scraper.pdf_bytes("https://j.example/c.pdf")
    assert len(seen) == 2


def test_robots_disallow_is_read_past_only_for_listed_brands(scraper, monkeypatch):
    monkeypatch.setattr(scraper, "_allowed", lambda u: False)
    seen = _spy(scraper, monkeypatch)
    _client(scraper, _status(200))
    scraper.brand = "Peugeot"
    with pytest.raises(RobotsDisallowed):
        scraper.page("https://k.example/")
    with pytest.raises(RobotsDisallowed):
        scraper.pdf_bytes("https://k.example/a.pdf")
    assert seen == []
    scraper.brand = "Mazda"
    assert scraper.cfg["official"]["playwright_robots_override"] == ["Mazda"]
    assert scraper.page("https://k.example/") == "<p>drawn</p>"
    assert scraper.pdf_bytes("https://k.example/a.pdf") == b"%PDF"
    assert seen == [("https://k.example/", False), ("https://k.example/a.pdf", "download")]
