---
tags: [active]
updated: 2026-09-30
---
# App Overview

Daily scrape of the Singapore car market, filtered and costed, sent to Telegram at 08:00 SGT.

* Entry point in the container: `scheduler.py` runs `main.py` daily and starts `bot_listener.py` (/run, /coe, /filters, /ask). `/ask <question>` sends the question and the last full report (`main.py` saves it as `last_report` in `sent_state`, `ai.py` `report_text`) to `claude -p` via `ai.py` `answer_question`, capped at `ai.ask_max_input_chars`.
* Delivery: `telegram_bot.py`. Set `TELEGRAM_THREAD_ID` to post into a forum topic; the listener then only answers inside that topic.
* Scrapers: `scrapers/coe.py` (Motorist first, LTA fallback), `scrapers/used_sgcarmart.py` and `scrapers/used_motorist.py` (Carro off), `scrapers/new_ev.py` (Sgcarmart electric car page), `scrapers/fuel_price.py` (Motorist grade board), `scrapers/registrations.py` (LTA M03 PDF, top selling brands). Each failure marks its section unavailable, the run continues. `scrapers/base.py` retries only transient errors (`_transient`). `ai.py` turns AI off for the run after the first CLI failure.
* Report sections (`report.py`, order in `config.yaml`): summary, COE position, Best Selling Top EV (brands ranked by EV registrations per body type, from the LTA M03 spreadsheet, with their models and prices), Used EV and Used Petrol Best Value lists, Top sellers in SG (Top EV brands and Top petrol brands, petrol includes hybrids), Pump prices, cost of ownership.
* Layout (`telegram_bot.py` `card`, `dot`, `note`): car lists are numbered cards with the car name as the link, COE and Top sellers are plain lines, only Pump prices and cost of ownership stay as `<pre>` tables at `telegram.table_width` 36. Method notes sit in a collapsed `<blockquote expandable>`. Long messages split on blank lines so a card is never cut.
* Change detection: `pipeline.py` `should_send`, sends only when watched sections changed.
* All filters and assumptions: `config.yaml`.

## Deployment

* Runs as the Dockge stack `sg-car-scraper` on the home NAS. Project folder is bind mounted at `/app`, so an update is copy files then Restart. A `requirements.txt` change needs a rebuild; a compose change needs Stop then Start.
* Bot: @owner_sgcar_bot, posting to the owner's group topic "SG EV Car Tracker".
* Logs: `logs/run.log` in the stack folder.

See [[Roadmap]] for what is not working against the live sites yet.
