# Project conventions for Claude Code

These are standing instructions from the repository owner. Follow them in every session.

## Git

* Work directly on `main`. Do not create feature branches or pull requests unless the owner
  asks for one. The application has not launched, so there is nothing to protect yet.
* Commit with clear messages and push to `origin main` after each completed piece of work.
* Delete any stray branch once its commits are on `main`.

## Diagrams

* Use draw.io for architecture and flow diagrams. Keep the source file under `docs/` as a
  `.drawio` file and reference it from the README. Render it with the draw.io tool when
  showing it in chat.

## Writing

* Do not use dashes as connectors (em dashes, en dashes, or hyphens between words) in
  generated text, comments, README content or Telegram output. Use commas, periods or
  restructured sentences. Hyphens inside identifiers, URLs and CLI flags are fine.

## Project

* Python 3.11 or newer with `uv`. Run `uv run pytest` before saying a piece of work is done.
* All filters, formulas and assumptions live in `config.yaml`, each block with a
  `verified_on` date. Do not hard code them.
* Scrapers honour `robots.txt`, throttle to one request every two seconds per domain, cache
  pages per day under `data/cache/`, and never bypass anti bot measures or logins.
