"""Optional AI assistance through the Claude Code CLI in print mode (claude -p).

The pipeline does not need a model: every field is read from page labels and every number is
arithmetic from config.yaml. The CLI is used only where rules fall short, and only when
ai.enabled is true in config.yaml:

* a detail page whose labels could not be read gets a second pass where the model is asked to
  pull the fields out of the page text as JSON,
* listing descriptions are checked for accident, as is, no warranty, scrap or export wording
  that the keyword list missed,
* optionally, a three sentence analyst note for the summary section.

Authentication is whatever the CLI already has: CLAUDE_CODE_OAUTH_TOKEN from `claude
setup-token` (uses your Claude subscription) or ANTHROPIC_API_KEY. Each run is capped by
ai.max_calls_per_run so a broken parser cannot turn into a large bill.
"""
from __future__ import annotations

import html
import json
import logging
import re
import shutil
import subprocess
from typing import Any, Optional

log = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)

LISTING_FIELDS = (
    "price", "depreciation_per_year", "reg_date", "mileage_km", "owners", "coe_expiry",
    "coe_years_remaining", "omv", "arf", "dereg_value", "engine_cc", "power_kw", "fuel_type",
    "seller_type", "battery_health", "flags",
)


class ClaudeCli:
    """Thin wrapper around `claude -p`. One call, no tools, JSON out."""

    def __init__(self, cfg: dict[str, Any]):
        ai = cfg.get("ai", {})
        self.enabled = bool(ai.get("enabled", False))
        self.command = ai.get("command", "claude")
        self.model = ai.get("model")
        self.timeout = int(ai.get("timeout_seconds", 120))
        self.max_chars = int(ai.get("max_input_chars", 12000))
        self.max_calls = int(ai.get("max_calls_per_run", 20))
        self.extra_args = list(ai.get("extra_args", []))
        self.calls = 0
        self.total_cost_usd = 0.0

    def available(self) -> bool:
        return self.enabled and shutil.which(self.command) is not None

    def ask(self, prompt: str, stdin_text: str = "") -> Optional[str]:
        """Run the CLI once and return its result text, or None when unavailable or failed."""
        if not self.available():
            return None
        if self.calls >= self.max_calls:
            log.warning("ai: call budget of %d per run reached", self.max_calls)
            return None
        self.calls += 1
        args = [self.command, "-p", prompt, "--output-format", "json", "--max-turns", "1", "--tools", "", "--no-session-persistence"]
        if self.model:
            args += ["--model", self.model]
        args += self.extra_args
        try:
            proc = subprocess.run(
                args, input=stdin_text[: self.max_chars], capture_output=True, text=True, timeout=self.timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("ai: claude cli failed: %s", exc)
            return None
        if proc.returncode != 0:
            # A failing CLI is almost always a missing login, so stop for the rest of the run
            # instead of spending the whole call budget on the same error.
            detail = (proc.stderr.strip() or proc.stdout.strip())[:300]
            log.warning("ai: claude cli exit %s, AI off for this run: %s", proc.returncode, detail)
            self.enabled = False
            return None
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout.strip() or None
        if isinstance(envelope, dict):
            self.total_cost_usd += float(envelope.get("total_cost_usd") or 0)
            if envelope.get("is_error"):
                log.warning("ai: cli reported an error: %s", str(envelope.get("result"))[:300])
                return None
            return str(envelope.get("result", "")).strip() or None
        return proc.stdout.strip() or None

    def ask_json(self, prompt: str, stdin_text: str = "") -> Optional[dict[str, Any]]:
        text = self.ask(prompt, stdin_text)
        if not text:
            return None
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        m = _JSON_BLOCK.search(text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None


def extract_listing_fields(cli: ClaudeCli, page_text: str) -> dict[str, Any]:
    """Ask the model to read a used car listing. Returns only the fields it could find."""
    prompt = (
        "You are given the visible text of a Singapore used car listing on standard input. "
        "Return one JSON object and nothing else, with these keys when the page states them, "
        "omitting any key that is not stated: " + ", ".join(LISTING_FIELDS) + ". "
        "Numbers must be plain numbers in SGD, km, cc or kW without symbols. Dates must be "
        "YYYY-MM-DD. fuel_type is one of electric, petrol, hybrid, diesel. seller_type is dealer "
        "or direct owner. flags is a list drawn from accident, as is, no warranty, scrap, export "
        "when the page states the car has that property; a statement that the car is accident "
        "free is not a flag. Do not use any tools."
    )
    data = cli.ask_json(prompt, page_text) or {}
    return {k: v for k, v in data.items() if k in LISTING_FIELDS and v not in (None, "", [])}


MEMORY_INTRO = ("\n\nWhat the tracker already did and learned, from its vault, newest first. Use it for price "
                "trends and days on market. A car the memory shows was already reported at the same price is "
                "not news, so do not flag it again:\n")


def with_memory(prompt: str, memory: str) -> str:
    return prompt + MEMORY_INTRO + memory if memory else prompt


def analyst_note(cli: ClaudeCli, facts: dict[str, Any], memory: str = "") -> Optional[str]:
    """Three plain sentences on today's report. Facts are the numbers already computed."""
    prompt = (
        "You are given today's Singapore car market figures as JSON on standard input: COE "
        "premiums, the best value new EV, the best used EV and the best used petrol or hybrid "
        "car with their annual costs, and what changed since the last report. Write at most "
        "three short plain sentences for a buyer deciding between them. No headings, no "
        "bullet points, no dashes. Do not invent figures. Do not use any tools."
    )
    text = cli.ask(with_memory(prompt, memory), json.dumps(facts, default=str))
    return text.strip() if text else None


def report_text(sections_html: list[str]) -> str:
    """Plain text of a built report, saved so /ask can answer from it."""
    return "\n\n".join(html.unescape(re.sub(r"<[^>]+>", "", h)).strip() for h in sections_html)


def answer_question(cli: ClaudeCli, question: str, report: str, memory: str = "") -> Optional[str]:
    """Answer a Telegram /ask question from the latest report, plus general car knowledge."""
    prompt = (
        "You help a buyer in Singapore choose a car. Standard input holds the latest report from "
        "their car tracker: COE premiums, new and used EV and petrol car lists with prices and "
        "running costs, top selling brands and pump prices. Answer this question in at most eight "
        "short plain sentences. Use the report's figures where they apply and say when you rely "
        "on general knowledge instead. Do not invent prices. No headings, no dashes, no markdown. "
        "Do not use any tools. Question: " + question
    )
    return cli.ask(with_memory(prompt, memory), report)
