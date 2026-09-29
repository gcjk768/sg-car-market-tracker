---
tags: [active]
updated: 2026-09-29
---
# Roadmap

* Carro: the `fuel=` filter no longer filters and listings sit in escaped page JSON (`detailUrl`, `price`, `mileage`, `owner_count`, `remaning_license_duration`). Detail pages read fine without Chromium. Find the new fuel filter, then parse cards from the JSON.
* New EV range is estimated (kWh times km/kWh). Add claimed WLTP figures for the always included models to `config.yaml` if the estimate misleads.
* A full run takes about an hour because Sgcarmart asks for a 30 s Crawl-delay. Move `schedule_time` earlier if the 08:00 report arrives too late.
* Sign the Claude CLI in on the NAS, or set `ai.enabled: false`. Without a login every run spends its 20 call budget on failures.
