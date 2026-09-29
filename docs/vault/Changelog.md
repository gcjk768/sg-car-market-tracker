---
tags: [active]
updated: 2026-09-29
---
# Changelog

## 2026-09-29

* Report reshaped on request: COE position table (change, quota, bids), New EV / Used EV / Used Petrol Car Best Value lists with deposit, instalment and depreciation columns (`report.py`), Pump prices table for every station and grade, Buying considerations dropped from `config.yaml` section order.
* Sgcarmart used: new search URLs (PARF cars `cat=18`, lowest depreciation first), rendered results page, new link pattern, COE years from the reg date line, cards over the price ceiling or under the COE minimum skipped before opening (`scrapers/used_sgcarmart.py`, `scrapers/used_common.py`).
* Sgcarmart new EVs: index is `/electric-vehicle`, variants from submodel blocks, range estimated as kWh times km/kWh (`scrapers/new_ev.py`).
* COE: date snaps to a real results Wednesday, Motorist first so the previous tender and change come from its change row (`scrapers/coe.py`); stored quota and bids survive derived rows (`db.py`).
* Fuel: Motorist grade board parsed into a new `grades` column (`scrapers/fuel_price.py`, `db.py`). Cnergy no longer publishes prices online.
* Flag words matched only on the car's own text; commercial vehicle types rejected (`filters.py`). Carro switched off in `config.yaml`.
* Motorist detail pages now read: sibling span labels in `scrapers/parse_utils.py`, headline price and `$/yr` depreciation in `scrapers/used_common.py`, page menus stripped before flag matching. Live fixture `fixtures/motorist_used_detail_live.html`.
* `scrapers/base.py` honours a site's robots.txt Crawl-delay (Sgcarmart asks 30 s).
* First live run on the NAS. COE and Motorist work. Sgcarmart, Carro, the new EV list and Cnergy do not parse the live sites yet, see [[Roadmap]].
* Forum topic support (`TELEGRAM_THREAD_ID`) in `telegram_bot.py`, `main.py`, `bot_listener.py`.
* `bot_listener.py` sleeps after a failed getUpdates instead of spinning, and long polls under the HTTP timeout.
* A bare `<a href>` no longer crashes `scrapers/new_ev.py` and `scrapers/used_common.py`.
* `main.py` logging uses `force=True`, so `logs/run.log` is written under `scheduler.py`.
* `pipeline.py` used section reason says nothing passed the filters, then only that group's failed sources.
* `tzdata` dependency so the tests run on Windows. `docker-compose.yml` memory cap and project folder bind mount.
