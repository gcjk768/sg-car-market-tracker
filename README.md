# SG car scraper

A Python project that runs once a day, scrapes the Singapore car market, filters listings
against your criteria, estimates total cost of ownership, and sends a formatted report to
Telegram with a clickable link to every listing.

Build status:

1. Scaffold, config, models, database, Telegram delivery and a sample report: done
2. COE scraper and COE section: done
3. Used listings scrapers (Sgcarmart, Carro, Motorist), filters, NEW and DROP tags: done
4. New EV price list scraper with brand page cross check: done
5. Cost of ownership engine and comparison table: done
6. Buying considerations, scheduling, listener, tests and README: done

Telegram delivery has not been exercised yet (on hold), and every scraper was written against
hand built fixtures because the build environment could not reach the sites. Read
"First run against the live sites" before relying on the output.

## Prerequisites

* Python 3.11 or newer.
* [uv](https://docs.astral.sh/uv/) for dependency management. If you do not have it, `pip install -r requirements.txt` works too.
* A Telegram account.
* Chromium for Playwright, used for the JavaScript rendered pages (Carro and the LTA COE page).

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

```bash
uv run python main.py --sample --dry-run   # print the sample report, no network, no sending
uv run python main.py --sample             # send the sample report to Telegram
uv run python main.py --dry-run            # scrape everything and print the report
uv run python main.py                      # scrape everything and send it
uv run python main.py --section coe --dry-run
uv run python main.py --force              # ignore today's page cache and resend
uv run python main.py --since 2026-09-20   # tag NEW and DROP relative to that date
```

By default the report is only sent when something changed since the last report that went
out: a new COE tender, a change in the new EV price list, or a change in either used car
shortlist (a new car, a car gone, a price drop). The job still runs every day so prices keep
being tracked. `telegram.send_only_on_change` in `config.yaml` turns this off,
`change_sections` chooses what is compared, and `heartbeat_after_days` sends a report anyway
after that many quiet days (0 keeps it silent). `--force` always sends. A dry run prints
whether the report would have been sent and why.

Rerunning on the same day is safe. Pages fetched today are cached under `data/cache/YYYY-MM-DD/`,
database writes are upserts, and the report is not resent unless you pass `--force`.

Console output uses `rich` panels. Each panel title shows the section key and its character
count so you can see how close a section is to the Telegram limit.

## Bot commands

`bot_listener.py` is optional and only runs while you keep it running:

```bash
uv run python bot_listener.py
```

Send `/run` for a full report now, `/coe` for the COE table only, `/filters` to see the current
used car filters. Messages from other chats are ignored.

## Scheduling

Cron, 08:00 Singapore time every day:

```
CRON_TZ=Asia/Singapore
0 8 * * * cd /path/to/EV-COE && /path/to/uv run python main.py >> logs/cron.log 2>&1
```

If your cron does not support `CRON_TZ`, set the machine time zone to Asia/Singapore or use
`0 0 * * *` on a UTC machine.

GitHub Actions: `.github/workflows/daily.yml` runs at 00:00 UTC (08:00 SGT), installs uv,
dependencies and Chromium, restores `data/cars.db` from the actions cache, runs the report and
saves the database back. Add `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and optionally
`SCRAPER_CONTACT` as repository secrets. The workflow can also be started by hand from the
Actions tab, with a dry run option.

Why the cache and not a commit back to the repository: committing a binary database every day
fills the git history with megabytes of noise and needs write permission for the workflow.
The cache is invisible and needs no permissions. The trade off is that GitHub evicts cache
entries after 7 days without use, so if the schedule stops for more than a week the history of
NEW and DROP tags starts again from empty. Daily runs keep it warm. If you want a permanent
record, add a step that uploads `data/cars.db` as an artifact or commits it to a separate branch.

## Deploy on a NAS with Docker

The image is built on the official Playwright image, so Chromium and its libraries come
ready. The container runs `scheduler.py`, which sends the report at `general.schedule_time`
(08:00 Singapore time by default) and, with `RUN_LISTENER=1`, answers `/run`, `/coe` and
`/filters`. No cron is needed on the NAS.

1. Copy the project folder to the NAS (for example `/volume1/docker/sg-car-scraper` on a
   Synology, or clone it there with git).
2. Create `.env` next to `docker-compose.yml` with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   and `SCRAPER_CONTACT`.
3. Build and start:

   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```

   `RUN_ON_START=1` in `docker-compose.yml` makes the container send a report straight away,
   so the first start doubles as the delivery test. Set it to `0` afterwards if you do not
   want a report on every restart.
4. `data/` (database and page cache) and `logs/` are mounted from the host, so they survive
   image rebuilds. `config.yaml` is mounted read only; edit it on the host and restart the
   container to pick up changes.

Synology Container Manager: create a project from the folder, it reads `docker-compose.yml`.
QNAP Container Station: use "Create application" and paste the compose file. Portainer works
the same way with a stack. The image is around 2 GB because of Chromium; the container needs
about 1 GB of RAM while a page renders.

To send a one off report or a dry run from inside the running container:

```bash
docker compose exec sg-car-scraper python main.py --dry-run
docker compose exec sg-car-scraper python main.py --force
```

## Optional AI help through the Claude Code CLI

The app does not need a model. Every field comes from page labels and every figure is
arithmetic from `config.yaml`. The `ai` block turns on three optional helpers that call the
Claude Code CLI in print mode (`claude -p`), which runs on your Claude subscription through a
token from `claude setup-token`, or on an API key:

* When a listing page loses the fields named in `ai.fallback_when_missing`, the page text is
  sent to the model and the JSON it returns fills the gaps. This is the layout change safety
  net: the report keeps working while you refresh the fixture and fix the parser.
* The same pass catches accident, as is, no warranty, scrap or export wording that the keyword
  list missed, and it knows that "accident free" is not a warning.
* `ai.analyst_note: true` adds three plain sentences to the summary section.

`ai.enabled` and `ai.analyst_note` are on by default and `docker-compose.yml` builds the image
with the CLI (`WITH_CLAUDE: "1"`).

### Sign in to the Claude CLI from the container

Once the container is running, open a terminal into it and log in. The CLI prints a link:
open it on your phone or laptop, approve, and paste the code back into the terminal.

```bash
docker compose exec -it sg-car-scraper claude auth login
docker compose exec -it sg-car-scraper claude auth status
```

On a Synology, the Container Manager "Terminal" tab on the container does the same thing
(run `claude auth login` there). The login is stored in `data/claude/` on the host, mounted
as the CLI's config directory, so it survives restarts and image rebuilds. Sign out with
`claude auth logout` from the same terminal.

If you would rather not log in interactively, run `claude setup-token` on any machine where
you use Claude Code and put the result in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`, or set
`ANTHROPIC_API_KEY` for API billing. Calls are capped per run by `ai.max_calls_per_run`, input is trimmed to
`ai.max_input_chars`, and every call runs with `--max-turns 1`, `--tools ""` (no tools) and
`--no-session-persistence`. A call costs about one cent at the CLI's default model. The run
log reports which fields the model filled. If the CLI is missing, the token is absent or a
call fails, the pipeline carries on without it, so a run never breaks because of the model.

## Run the tests

```bash
uv run pytest
```

The suite covers every parser against a fixture in `fixtures/`, every filter, every cost
formula with known inputs, the next tender date calculation, the Telegram formatter (length
limit, table width, link numbering, balanced `<pre>` blocks), the database upserts, change
detection and the scheduler. `tests/test_e2e.py` runs the whole program end to end: a local
web server serves the fixture pages under each site's URL patterns, Chromium renders the
Carro pages, and a local mock of the Telegram Bot API receives the messages, which are then
checked for size, balanced tags and numbered links. The same run proves the same day rerun is
idempotent and that `--force` resends.

## Changing filters and cost assumptions

Everything lives in `config.yaml`:

* `used.filters` holds the price ceiling, mileage per year, owner count, COE years remaining,
  age limits and the keyword exclusions and bonuses.
* `used.searches` holds the search URL per site and drivetrain group. Apply your filters on the
  site, then paste the URL of the first results page with `{page}` in place of the page number.
* `new_ev` holds the ranking score, the models that are always shown and the VES rebates.
* `costs` holds every road tax band, the insurance lookup table, both PARF schedules, the ARF
  tiers, energy prices, the loan rules and flat rates, and fixed extras. Each block has a
  `verified_on` date. Update the date when you check the values against LTA, MAS or IRAS.
* `costs.energy.ice.price_pick` chooses which brand's 95 octane price feeds the running cost:
  `median` (default), `min` or `avg`. Cnergy is included in the comparison but sells far below
  the majors at a handful of stations, which is why the default is the median.

Prices in the report: new EV prices are with COE and net of the VES and EEAI rebates, which is
how dealers advertise them, and the section says so. Pump prices are the listed prices before
card or loyalty discounts. Deposits are the minimum under the MAS loan to value rules and
instalments use flat rates, so the effective interest rate is higher than the figure shown.
* `buying_considerations` holds the reference text printed at the end of the report.

## Architecture

The architecture diagram lives in `docs/architecture.drawio`. Open it at
[app.diagrams.net](https://app.diagrams.net) or with the draw.io desktop app or VS Code
extension. In one line: scrapers fetch and parse each source through a shared base with
robots.txt, throttling and a daily cache; the pipeline stores everything in SQLite, applies
the filters and cost formulas, and hands seven sections to the Telegram sender.

## Project layout

```
CLAUDE.md             standing conventions for Claude Code sessions
docs/architecture.drawio  architecture diagram (draw.io)
config.yaml           all filters, formulas and assumptions
.env.example          secrets template
main.py               CLI entry point
pipeline.py           runs the scrapers, persists results, assembles the sections
settings.py           config and secrets loading
models.py             pydantic models for every scraped record
db.py                 SQLite schema and upserts, file at data/cars.db
filters.py            used car filters, ranking and NEW and DROP tags
costs.py              road tax, PARF, ARF, depreciation, energy, insurance bands
report.py             section builders, console rendering and the sample report
telegram_bot.py       Bot API client, fixed width tables, link lists, message splitting
bot_listener.py       optional long polling command listener
scrapers/base.py      fetch with retry, robots.txt, throttling and the daily page cache
scrapers/parse_utils.py  label and number parsing helpers shared by all scrapers
scrapers/coe.py       COE results from OneMotoring, Sgcarmart or Motorist, next tender date
scrapers/used_common.py  shared used car scraper logic
scrapers/used_sgcarmart.py, used_carro.py, used_motorist.py
scrapers/new_ev.py    new EV price list and brand page cross check
scrapers/fuel_price.py  daily 95 octane price per brand from a comparison page
scrapers/fuel_cnergy.py  Cnergy price board, public and member prices per grade
fixtures/             HTML pages used by parser tests
tests/                pytest suite
data/                 database and page cache (ignored by git)
logs/                 run.log
```

## First run against the live sites

Every file in `fixtures/` is a hand built page that mirrors the layout each site is believed
to use. The parsers lean on labels ("Mileage", "COE", "OMV") and number patterns rather than
exact CSS paths, so they have a fair chance on the real pages, but they have not been proven.
Do this once on a machine that can reach the sites:

1. Run `uv run python main.py --dry-run --section coe`. If the COE table is empty, open the
   page in a browser, save it into `fixtures/onemotoring_coe.html` (see the next section) and
   look at how the table is laid out.
2. Repeat for `--section used` and `--section new`. The log in `logs/run.log` names the URL
   that failed and why.
3. Check `used.searches` in `config.yaml`. The search parameters are guesses. Apply your
   filters on each site and paste the real URL.

## When a site changes layout

1. Save the live page exactly as the scraper sees it. For static pages:
   `uv run python -c "from scrapers.base import BaseScraper; ..."` is more than you need; the
   simplest way is the browser: open the page, right click, "Save page as", "Webpage, HTML only",
   into `fixtures/<site>_<kind>.html`. For rendered pages (Carro) use the browser's developer
   tools, copy the outer HTML of the document after it has loaded, and save that.
2. Run `uv run pytest tests/test_<scraper>.py`. The failing assertion tells you which field
   stopped parsing.
3. Fix the parser. Most fixes are a new label synonym in the scraper's `labels` dictionary or
   a new link pattern in `listing_href`. Keep the old fixture if the site serves both layouts.
4. Run the full suite and a dry run before the next scheduled run.

The daily page cache under `data/cache/YYYY-MM-DD/` also holds every page fetched today, which
is a quick source of fresh fixtures.

## Terms of use per source

Read before you decide to keep scraping a site. This was written without being able to open
the sites, so confirm each entry against the live terms page and `robots.txt` (the scraper
already refuses any path that `robots.txt` disallows for its user agent).

* LTA OneMotoring (COE results): government site. The terms of use allow personal,
  non commercial use of the information. The lightest approach is LTA's open dataset on
  data.gov.sg ("COE Bidding Results"), which has a JSON API and no scraping at all. Switching to
  it means adding a small client in `scrapers/coe.py`; the table parser already handles the
  same fields.
* Sgcarmart: a commercial classifieds site whose terms of use, as far as can be determined,
  prohibit automated access, crawlers and data extraction without written consent. Treat the
  scraper as a personal convenience at low volume (three result pages and up to forty detail
  pages per search, one request every two seconds or more) and stop if they object. A lighter
  alternative is their email or app alerts for saved searches.
* Carro: a commercial marketplace with a JavaScript app. Its terms are believed to prohibit
  automated access. Same guidance as Sgcarmart. Carro publishes price alerts in its app.
* Motorist: a commercial listing portal with articles and a COE results page. Its terms are
  believed to prohibit automated extraction of listings. The COE and petrol price pages are
  editorial and low volume. Same guidance.
* Brand pages (BYD, Tesla, MG, GAC Aion, Xpeng, Zeekr, Deepal, Hyundai, Kia, Polestar): one
  request each per day to read a public price. Marketing pages rarely object to this, but
  check `robots.txt`, which the scraper honours automatically.
* Petrol prices: Motorist's petrol page and petrolprice.sg are comparison pages updated for
  public reading. One request per day.
* Cnergy (cnergy.sg): the station's own price board, one request per day. Its terms could
  not be read from the build environment. Cnergy prices are shown on their own line in the
  cost section, with the member price, and `preferred_station` in `config.yaml` controls it.

Nothing in this project bypasses anti bot measures, CAPTCHAs or login walls. If a site
disallows automated access, remove it from `coe.source_order` or `used.searches` in
`config.yaml`.

## Verification status

Checked on 2026-09-29 through web search summaries of LTA, MAS and press pages (the pages
themselves could not be opened from the build environment):

* Road tax formulas are published per 6 months with the 0.782 factor. Annual tax is the formula
  doubled. Fully electric cars add a 700 per year flat component. Petrol electric cars pay the
  higher of the engine capacity and power rating formulas, with no flat component.
* PARF: cars registered before 15 February 2023 get 75 to 50 percent of ARF with no cap; from
  15 February 2023 the cap is 60,000; cars with COEs from the second February 2026 exercise get
  30 to 5 percent, capped at 30,000. ARF tiers are 100, 140, 190, 250 and 320 percent.
* Cat A covers non electric cars up to 1,600 cc and 97 kW and electric cars up to 110 kW.
  Bidding opens on the first and third Monday of each month and closes on the Wednesday.
* Loan to value is 70 percent for OMV up to 20,000 and 60 percent above it, maximum 7 years.
* EEAI: 45 percent off ARF capped at 7,500 for 2026, ends 31 December 2026. VES: single band A
  for electric cars, 22,500 in 2026 and 20,000 in 2027, hybrids get nothing from 2026.
* Latest tender: 23 September 2026, Cat A 131,890, Cat B 133,000, Cat C 92,144, Cat E 137,000.

Still not verified:

* Insurance premium bands, which are indicative only. Get a quote from the comparison links in
  the report.
* Every source URL and search parameter in `config.yaml`, and every fixture layout.
* Public holidays that shift a tender by a day are not modelled in the next tender date.
