---
tags: [active]
updated: 2026-09-30
---
# Changelog

## 2026-09-30

* `ai.py`: a failed `claude` call switches AI off for the rest of the run and logs stdout when stderr is empty. The unsigned CLI on the NAS was spending all 20 calls per run on the same login error.
* `scrapers/base.py`: only transient errors are retried (network, 403, 429, 5xx), via `_transient`. A 404 was fetched three times. `scrapers/used_common.py` logs a 404 past page 1 as the normal end of Motorist results, not a warning.
* Docs: README rewritten (highlights, flow, stack, limitations) and a new draw.io architecture diagram, `docs/architecture.drawio` with `docs/architecture.drawio.svg` and `docs/architecture.png` exports.
* Telegram layout made phone friendly on request. The 60 character `<pre>` tables wrapped on a phone and every car was listed twice (table row, then a numbered link). Car lists are now cards, one bold linked name plus three short lines (`report.py`, `telegram_bot.py` `card`, `dot`). COE and Top sellers are plain lines. Pump prices and cost of ownership tables are 32 and 36 wide (`telegram.table_width` 60 to 36). Method notes are collapsed quotes. `split_message` splits on blank lines first.
* Longer lists on request, about 20 cars each: `used.top_n` 8 to 20, `new_ev.top_n_per_body_type` 4 to 6 (`config.yaml`). The real limit was the candidate pool (70 used EVs gave 4 that passed the filters), so Motorist searches now pre filter by `price_max` and `year_min`, and `max_list_pages` 3 to 5, `max_detail_pages_per_search` 40 to 80.
* New Top sellers in SG section: brands ranked by new registrations this year from LTA table M03, a PDF read with `pypdf` (`scrapers/registrations.py`, `report.py` `top_sellers_section`, `pipeline.py`). LTA does not publish registrations by model.

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
