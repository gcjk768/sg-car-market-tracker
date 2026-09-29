---
tags: [active]
updated: 2026-09-29
---
# Changelog

## 2026-09-29

* Motorist detail pages now read: sibling span labels in `scrapers/parse_utils.py`, headline price and `$/yr` depreciation in `scrapers/used_common.py`, page menus stripped before flag matching. Live fixture `fixtures/motorist_used_detail_live.html`.
* `scrapers/base.py` honours a site's robots.txt Crawl-delay (Sgcarmart asks 30 s).
* First live run on the NAS. COE and Motorist work. Sgcarmart, Carro, the new EV list and Cnergy do not parse the live sites yet, see [[Roadmap]].
* Forum topic support (`TELEGRAM_THREAD_ID`) in `telegram_bot.py`, `main.py`, `bot_listener.py`.
* `bot_listener.py` sleeps after a failed getUpdates instead of spinning, and long polls under the HTTP timeout.
* A bare `<a href>` no longer crashes `scrapers/new_ev.py` and `scrapers/used_common.py`.
* `main.py` logging uses `force=True`, so `logs/run.log` is written under `scheduler.py`.
* `pipeline.py` used section reason says nothing passed the filters, then only that group's failed sources.
* `tzdata` dependency so the tests run on Windows. `docker-compose.yml` memory cap and project folder bind mount.
