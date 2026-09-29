---
tags: [active]
updated: 2026-09-29
---
# App Overview

Daily scrape of the Singapore car market, filtered and costed, sent to Telegram at 08:00 SGT.

* Entry point in the container: `scheduler.py` runs `main.py` daily and starts `bot_listener.py` (/run, /coe, /filters).
* Delivery: `telegram_bot.py`. Set `TELEGRAM_THREAD_ID` to post into a forum topic; the listener then only answers inside that topic.
* Scrapers: `scrapers/coe.py`, `scrapers/used_*.py`, `scrapers/new_ev.py`, `scrapers/fuel_*.py`. Each failure marks its section unavailable, the run continues.
* Change detection: `pipeline.py` `should_send`, sends only when watched sections changed.
* All filters and assumptions: `config.yaml`.

## Deployment

* Runs as the Dockge stack `sg-car-scraper` on the home NAS. Project folder is bind mounted at `/app`, so an update is copy files then Restart. A `requirements.txt` change needs a rebuild; a compose change needs Stop then Start.
* Bot: @owner_sgcar_bot, posting to the owner's group topic "SG EV Car Tracker".
* Logs: `logs/run.log` in the stack folder.

See [[Roadmap]] for what is not working against the live sites yet.
