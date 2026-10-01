---
tags: [active]
updated: 2026-10-01
---
# App Overview

Daily scrape of the Singapore car market, filtered and costed, sent to Telegram at 08:00 SGT.

* Entry point in the container: `scheduler.py` supervises `main.py` (daily, as a child process with a time limit, retried on failure) and `bot_listener.py` (/run, /coe, /filters, /ask), writes `data/heartbeat` for the healthcheck, and after a second failure starts `heal.py`, which repairs `scrapers/` through `claude -p` and keeps the change only if the tests pass and the sections recover. `/ask <question>` sends the question and the last full report (`main.py` saves it as `last_report` in `sent_state`, `ai.py` `report_text`) to `claude -p` via `ai.py` `answer_question`, capped at `ai.ask_max_input_chars`.
* Delivery: `telegram_bot.py`. Set `TELEGRAM_THREAD_ID` to post into a forum topic; the listener then only answers inside that topic.
* Scrapers: `scrapers/coe.py` (Motorist first, LTA fallback), `scrapers/used_sgcarmart.py` and `scrapers/used_motorist.py` (Carro off), `scrapers/new_ev.py` (Sgcarmart electric car page), `scrapers/fuel_price.py` (Motorist grade board), `scrapers/registrations.py` (LTA M03 PDF, top selling brands). Each failure marks its section unavailable, the run continues. `scrapers/base.py` retries only transient errors (`_transient`, never a 403 block). `scrapers/used_common.py` skips sold cars, fills `{price_max}` and `{year_min}` in the search URLs from the filters, and calls the AI fallback only for fields the page does not label. `ai.py` turns AI off for the run after the first CLI failure.
* Report sections (`report.py`, order in `config.yaml`): summary, COE position, Best Selling Top EV (brands ranked by EV registrations per body type, from the LTA M03 spreadsheet, with their models and prices), Best Selling Used EV and Best Selling Used Petrol Car (filtered cars in order of their brand's new registrations, then lowest depreciation), Top sellers in SG (Best selling EV of each brand, its model from the hand kept `config.yaml` `top_sellers.ev_models`, and Top petrol brands, petrol includes hybrids), Pump prices, cost of ownership.
* Layout (`telegram_bot.py` `card`, `dot`, `note`): section titles start with an emoji, tags are 🆕 NEW and 🟢 DROP, COE moves are 🟢 fall and 🔴 rise, and the last report message has 🔄 Run again and 🎫 COE only buttons (`telegram_bot.py` `REPORT_BUTTONS`, handled by `bot_listener.py` `parse_update`). car lists are numbered cards with the car name as the link, COE and Top sellers are plain lines, only Pump prices and cost of ownership stay as `<pre>` tables at `telegram.table_width` 36. Method notes sit in a collapsed `<blockquote expandable>`. Long messages split on blank lines so a card is never cut.
* Change detection: `pipeline.py` `should_send`, sends only when watched sections changed.
* All filters and assumptions: `config.yaml`.

## Deployment

* Runs as the Dockge stack `sg-car-scraper` on the home NAS. Project folder is bind mounted at `/app`, so an update is copy files then Restart. A `requirements.txt` change needs a rebuild; a compose change needs Stop then Start.
* Bot: @owner_sgcar_bot, posting to the owner's group topic "SG EV Car Tracker".
* Logs: `logs/run.log` in the stack folder.

See [[Roadmap]] for what is not working against the live sites yet.
