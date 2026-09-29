# SG car scraper

A Python project that runs once a day, scrapes the Singapore car market, filters listings
against your criteria, estimates total cost of ownership, and sends a formatted report to
Telegram with a clickable link to every listing.

Build status by stage:

1. Scaffold, config, models, database, Telegram delivery and a sample report: done
2. COE scraper and COE section: pending
3. Used listings scrapers (Sgcarmart, Carro, Motorist), filters, NEW and DROP tags: pending
4. New EV price list scraper: pending
5. Cost of ownership engine and comparison table: pending
6. Buying considerations, scheduling, full tests and README: pending

## Prerequisites

* Python 3.11 or newer.
* [uv](https://docs.astral.sh/uv/) for dependency management. If you do not have it, `pip install -r requirements.txt` works too.
* A Telegram account.
* Chromium for Playwright, needed from stage 3 onwards for JavaScript rendered pages.

## Install

```bash
uv sync
uv run playwright install chromium
```

Without uv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Create the Telegram bot

1. Open Telegram and start a chat with `@BotFather`.
2. Send `/newbot`, choose a display name, then a username that ends in `bot`.
3. BotFather replies with a token that looks like `123456789:AAH...`. That is `TELEGRAM_BOT_TOKEN`.
4. Open a chat with your new bot and send it any message, for example `hello`. The bot cannot message you until you have done this.

## Find your chat ID

After you have messaged the bot, open this URL in a browser with your token filled in:

```
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
```

Look for `"chat":{"id":123456789,...}` in the response. That number is `TELEGRAM_CHAT_ID`.
If the response is empty, send the bot another message and reload.

## Set up .env

```bash
cp .env.example .env
```

Fill in `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and, optionally, `SCRAPER_CONTACT`. The contact
is included in the User Agent string so site owners can reach you. Never commit `.env`.

## Run

Print the sample report to the console without sending anything:

```bash
uv run python main.py --sample --dry-run
```

Send the sample report to Telegram to confirm delivery works:

```bash
uv run python main.py --sample
```

Daily run (scrapers arrive in later stages, sections without a scraper show as unavailable):

```bash
uv run python main.py            # scrape, build and send
uv run python main.py --dry-run  # scrape, build and print only
uv run python main.py --section coe --dry-run
uv run python main.py --force    # ignore the page cache and resend even if already sent today
uv run python main.py --since 2026-09-20
```

Rerunning on the same day is safe. Pages fetched today are cached under `data/cache/YYYY-MM-DD/`,
database writes are upserts, and the report is not resent unless you pass `--force`.

## Run the tests

```bash
uv run pytest
```

## Changing filters and cost assumptions

Everything lives in `config.yaml`:

* `used.filters` holds the price ceiling, mileage per year, owner count, COE years remaining,
  age limits and the keyword exclusions and bonuses.
* `new_ev` holds the ranking score and the models that are always shown.
* `costs` holds every road tax band, the insurance lookup table, PARF and ARF schedules,
  energy prices and fixed extras. Each block has a `verified_on` date. Update the date when you
  check the values against LTA or IRAS.
* `buying_considerations` holds the reference text printed at the end of the report.

## Project layout

```
config.yaml        all filters, formulas and assumptions
.env.example       secrets template
main.py            CLI entry point
settings.py        config and secrets loading
models.py          pydantic models for every scraped record
db.py              SQLite schema and upserts, file at data/cars.db
report.py          section builders, console rendering and the sample report
telegram_bot.py    Bot API client, fixed width tables, link lists, message splitting
scrapers/base.py   fetch with retry, robots.txt, throttling and the daily page cache
fixtures/          saved HTML pages used by parser tests
tests/             pytest suite
data/              database and page cache (ignored by git)
logs/              run.log
```

The modules `costs.py`, `bot_listener.py`, the individual scrapers and the GitHub Actions
workflow are added in later stages.

## When a site changes layout

To be written in stage 6, together with the fixture refresh procedure.

## Terms of use per source

To be written in stage 6 once each source has been fetched and its robots.txt and terms read.
Nothing in this project bypasses anti bot measures, CAPTCHAs or login walls. If a site
disallows automated access, the scraper for it is switched off in `config.yaml`.

## Not yet verified

These values were seeded from memory and must be checked against the official pages before
you rely on the numbers. Each is marked in `config.yaml` with `verified_on: null`.

* Road tax bands and the 0.782 factor, the EV additional flat component of 700 per year, and
  how petrol electric cars are taxed (LTA road tax page).
* PARF percentages, the 60,000 cap and its effective date, and the ARF tiers (LTA and IRAS).
* Cat A power ceiling of 110 kW for electric cars (LTA COE categories).
* Loan to value limits and the 7 year tenure (MAS).
* EV Early Adoption Incentive end date and the current VES bands (LTA).
* Insurance premium bands, which are indicative only.
* Every source URL in `config.yaml`, because the build environment could not reach the sites.
