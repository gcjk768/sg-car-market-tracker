---
tags: [active]
updated: 2026-09-30
---
# Roadmap

* Carro: the `fuel=` filter no longer filters and listings sit in escaped page JSON (`detailUrl`, `price`, `mileage`, `owner_count`, `remaning_license_duration`). Detail pages read fine without Chromium. Find the new fuel filter, then parse cards from the JSON.
* New EV range is estimated (kWh times km/kWh). Add claimed WLTP figures for the always included models to `config.yaml` if the estimate misleads.
* A full run takes about an hour because Sgcarmart asks for a 30 s Crawl-delay. Move `schedule_time` earlier if the 08:00 report arrives too late.
* Sign the Claude CLI in on the NAS, or set `ai.enabled: false`. Without a login AI is skipped after the first failed call each run, and self repair cannot run.
* Self repair keeps changes only on the NAS. When a Telegram notice says a change was kept, bring the `git diff` from the NAS project folder into the repository, then stash it there before the next `git pull`.
* Cnergy no longer publishes pump prices on its site (checked 2026-09-30, the home page is a station finder). The Cnergy line stays empty until a source exists; drop `preferred_station` to stop the daily fetch.
