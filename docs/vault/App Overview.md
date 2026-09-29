---
tags: [active]
updated: 2026-09-29
---
# App Overview

Daily scrape of the Singapore car market, filtered and costed, sent to Telegram at 08:00 SGT.

* Entry point in the container: `scheduler.py` runs `main.py` daily and starts `bot_listener.py` (/run, /coe, /filters).
* Delivery: `telegram_bot.py`. Set `TELEGRAM_THREAD_ID` to post into a forum topic; the listener then only answers inside that topic.
* Scrapers: `scrapers/coe.py` (Motorist first, LTA fallback), `scrapers/used_sgcarmart.py` and `scrapers/used_motorist.py` (Carro off), `scrapers/new_ev.py` (Sgcarmart electric car page), `scrapers/fuel_price.py` (Motorist grade board). Each failure marks its section unavailable, the run continues.
* Report sections (`report.py`, order in `config.yaml`): summary, COE position, three Best Value lists, Pump prices, cost of ownership.
* Change detection: `pipeline.py` `should_send`, sends only when watched sections changed.
* All filters and assumptions: `config.yaml`.

## Deployment

* Runs as the Dockge stack `sg-car-scraper` on the home NAS. Project folder is bind mounted at `/app`, so an update is copy files then Restart. A `requirements.txt` change needs a rebuild; a compose change needs Stop then Start.
* Bot: @owner_sgcar_bot, posting to the owner's group topic "SG EV Car Tracker".
* Logs: `logs/run.log` in the stack folder.

See [[Roadmap]] for what is not working against the live sites yet.
