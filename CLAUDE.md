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

## Web access (Agent Reach)

* When you need the web during development (finding a new source, checking why a page changed,
  researching), use Agent Reach on the owner's PC (`/agent-reach` skill, `agent-reach doctor` first).
  Backends: `curl https://r.jina.ai/<URL>` for any page, Exa through `mcporter` for search,
  feedparser for RSS, yt-dlp for YouTube, `gh` for GitHub, OpenCLI for Reddit and X.
* New source code in this app starts from those same backends (an official API or feed first,
  then Jina Reader or feedparser) before writing an ad hoc scraper.
* Do not replace a scraper that already works and has tests. Agent Reach is not installed in the
  NAS container, so runtime code must keep working without it.
* Never use Jina or any proxy to get around a 403, a bot challenge or a robots.txt block.
  Credentials stay in `~/.agent-reach/`, never in this repo.
* Setup and fixes: Obsidian Vault note `Tools/Agent Reach Setup`.
