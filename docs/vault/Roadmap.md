---
tags: [active]
updated: 2026-09-30
---
# Roadmap

* Best Selling Top EV reads LTA's M03 spreadsheet (`sources.lta_registrations_by_make_xlsx`), which could not be opened from the build environment. `fixtures/lta_m03.xlsx` is synthetic. After the first NAS run, save the live file over it and rerun `uv run pytest tests/test_best_selling.py`. If the header row differs, adjust `parse_body_types_rows` in `scrapers/registrations.py`. Until it reads, the list falls back to value order and the summary names it as unavailable.
* Carro: the `fuel=` filter no longer filters and listings sit in escaped page JSON (`detailUrl`, `price`, `mileage`, `owner_count`, `remaning_license_duration`). Detail pages read fine without Chromium. Find the new fuel filter, then parse cards from the JSON.
* New EV range is estimated (kWh times km/kWh). Add claimed WLTP figures for the always included models to `config.yaml` if the estimate misleads.
* A full run takes about an hour because Sgcarmart asks for a 30 s Crawl-delay. Move `schedule_time` earlier if the 08:00 report arrives too late.
* Sign the Claude CLI in on the NAS, or set `ai.enabled: false`. Without a login AI is skipped after the first failed call each run.
