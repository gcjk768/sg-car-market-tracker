---
tags: [active]
updated: 2026-09-29
---
# Roadmap

* Sgcarmart used: search URL is now `/used-cars/listing` (old `fue=4` returns nothing), results render with JavaScript, links are `/used-cars/info/<slug>-<id>/`. New fuel filter parameter still unknown.
* Rewrite the Sgcarmart new car and used listing parsers for the new site layout (`/new-cars/...`). The index now yields promo links, not model pages.
* Carro: results page finds no listings. Check the live buy URL and markup.
* Cnergy price board could not be read.
* Save a live page per site under `fixtures/` when fixing each parser, so tests match reality.
* Sign the Claude CLI in on the NAS, or set `ai.enabled: false`. Without a login every run spends its 20 call budget on failures.
