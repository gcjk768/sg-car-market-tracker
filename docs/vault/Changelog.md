---
tags: [active]
updated: 2026-09-29
---
# Changelog

## 2026-09-29

* First live run on the NAS. COE and Motorist work. Sgcarmart, Carro, the new EV list and Cnergy do not parse the live sites yet, see [[Roadmap]].
* Forum topic support (`TELEGRAM_THREAD_ID`) in `telegram_bot.py`, `main.py`, `bot_listener.py`.
* `bot_listener.py` sleeps after a failed getUpdates instead of spinning, and long polls under the HTTP timeout.
* A bare `<a href>` no longer crashes `scrapers/new_ev.py` and `scrapers/used_common.py`.
* `main.py` logging uses `force=True`, so `logs/run.log` is written under `scheduler.py`.
* `pipeline.py` used section reason says nothing passed the filters, then only that group's failed sources.
* `tzdata` dependency so the tests run on Windows. `docker-compose.yml` memory cap and project folder bind mount.
