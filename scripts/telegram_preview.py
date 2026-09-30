"""Preview the Telegram report on a phone, rendered from the exact HTML the bot sends.

Builds the sample report (sample data, no network), splits it into messages the way the bot
does, lays the messages out in a Telegram style chat 390 pixels wide, and saves one PNG per
group of sections. It is a mock up of Telegram's look: fonts and colours are close, the text,
links and tables are exactly what the bot sends.

  uv run python scripts/telegram_preview.py              # writes docs/telegram_preview/
  uv run python scripts/telegram_preview.py /some/dir    # writes there instead

Chromium comes from Playwright, or from CHROMIUM_EXECUTABLE_PATH when that is set.
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from report import sample_report  # noqa: E402
from settings import load_config  # noqa: E402
from telegram_bot import split_message  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "telegram_preview"
OUT.mkdir(parents=True, exist_ok=True)
cfg = load_config(ROOT / "config.yaml")
sections = sample_report(cfg, date(2026, 9, 30))
messages = {}
for s in sections:
    html = s.html.replace("<i>SAMPLE DATA, delivery test</i>\n", "", 1)
    messages[s.key] = split_message(html)

GROUPS = [
    ("1_summary_coe", ["summary", "coe"]),
    ("2_best_selling_top_ev", ["new_ev"]),
    ("3_best_selling_used", ["used_ev", "used_ice"]),
    ("4_top_sellers_pump_costs", ["top_sellers", "fuel", "costs"]),
]

CSS = """
* { box-sizing: border-box; }
body { margin: 0; width: 390px; background: #cfe0c3;
  background-image: radial-gradient(rgba(255,255,255,.25) 1px, transparent 1px); background-size: 14px 14px;
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.bar { position: sticky; top: 0; background: #fff; padding: 10px 14px; display: flex; align-items: center; gap: 10px;
  box-shadow: 0 1px 2px rgba(0,0,0,.12); }
.avatar { width: 38px; height: 38px; border-radius: 50%; background: #3a8fd9; color: #fff; font-weight: 600;
  display: flex; align-items: center; justify-content: center; font-size: 15px; }
.name { font-weight: 600; font-size: 16px; } .sub { color: #8a8a8a; font-size: 13px; }
.chat { padding: 10px 8px 14px; }
.msg { background: #fff; border-radius: 14px 14px 14px 4px; padding: 7px 10px 18px; margin: 6px 40px 6px 0;
  font-size: 15px; line-height: 1.32; color: #000; position: relative; box-shadow: 0 1px 1px rgba(0,0,0,.12);
  overflow-wrap: anywhere; }
.time { position: absolute; right: 9px; bottom: 4px; font-size: 11.5px; color: #9aa4ab; }
a { color: #168acd; text-decoration: none; }
pre { font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; font-size: 12.5px; line-height: 1.35;
  background: #f1f3f5; border-radius: 6px; padding: 6px 8px; margin: 2px 0; white-space: pre; overflow-x: auto; }
blockquote { margin: 2px 0; padding: 4px 26px 4px 9px; border-left: 3px solid #3a8fd9; background: #eaf3fb;
  border-radius: 4px; position: relative; line-height: 20px; max-height: 68px; overflow: hidden; font-size: 14.5px; }
blockquote::after { content: "⌄"; position: absolute; right: 7px; bottom: 2px; color: #3a8fd9; font-size: 15px; }
u { text-decoration-thickness: 1px; }
"""


def to_browser(html: str) -> str:
    """Telegram treats newlines as line breaks. Keep <pre> blocks as they are."""
    parts = re.split(r"(<pre>.*?</pre>)", html, flags=re.S)
    out = []
    for p in parts:
        if p.startswith("<pre>"):
            out.append(p)
        else:
            out.append(p.replace("\n", "<br>"))
    return "".join(out).replace("<blockquote expandable>", "<blockquote>")


def page(keys, with_bar):
    bubbles = []
    for k in keys:
        for m in messages.get(k, []):
            bubbles.append(f'<div class="msg">{to_browser(m)}<span class="time">08:00</span></div>')
    bar = ('<div class="bar"><div class="avatar">SG</div><div><div class="name">SG Car Market</div>'
           '<div class="sub">bot</div></div></div>') if with_bar else ""
    return f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{bar}<div class='chat'>{''.join(bubbles)}</div></body></html>"


with sync_playwright() as p:
    exe = os.getenv("CHROMIUM_EXECUTABLE_PATH")
    b = p.chromium.launch(**({"executable_path": exe} if exe else {}))
    pg = b.new_page(viewport={"width": 390, "height": 800}, device_scale_factor=2)
    for i, (name, keys) in enumerate(GROUPS):
        pg.set_content(page(keys, with_bar=(i == 0)))
        pg.wait_for_timeout(200)
        path = OUT / f"{name}.png"
        pg.screenshot(path=str(path), full_page=True)
        h = pg.evaluate("document.body.scrollHeight")
        print(path.name, "css height", h, "messages", sum(len(messages.get(k, [])) for k in keys))
    b.close()
