from telegram_bot import (
    Column,
    escape,
    fmt_delta,
    link_list,
    pre_block,
    render_table,
    split_message,
    truncate,
)


def test_truncate_adds_ellipsis_only_when_needed():
    assert truncate("abc", 5) == "abc"
    assert truncate("abcdef", 5) == "abcd…"
    assert truncate("abcdef", 1) == "…"
    assert truncate(None, 3) == ""


def test_escape_handles_html_and_none():
    assert escape("<b> & </b>") == "&lt;b&gt; &amp; &lt;/b&gt;"
    assert escape(None) == ""


def test_render_table_respects_width_and_alignment():
    cols = [Column("#", 2, "right"), Column("Name", 30), Column("Price", 8, "right")]
    rows = [[1, "A very long car name that keeps going and going", 123456], [2, "Short", 99]]
    text = render_table(cols, rows, max_width=40)
    lines = text.split("\n")
    assert all(len(line) <= 40 for line in lines)
    assert lines[0].startswith(" #")
    assert lines[2].endswith("123,456") is False  # numbers are passed through as given
    assert lines[2].rstrip().endswith("123456")
    assert lines[3].startswith(" 2 Short")
    assert "…" in lines[2]


def test_render_table_narrows_widest_text_column_first():
    cols = [Column("Cat", 3), Column("Label", 50), Column("Num", 8, "right")]
    text = render_table(cols, [["A", "x" * 60, "1"]], max_width=30)
    for line in text.split("\n"):
        assert len(line) <= 30
    assert text.split("\n")[2].rstrip().endswith("1")


def test_link_list_numbers_match_rows():
    items = [("Car one", "https://example.com/1?a=1&b=2"), ("Car <two>", "https://example.com/2")]
    text = link_list(items)
    lines = text.split("\n")
    assert lines[0].startswith('1. <a href="https://example.com/1?a=1&amp;b=2">Car one</a>')
    assert lines[1] == '2. <a href="https://example.com/2">Car &lt;two&gt;</a>'


def test_fmt_delta_arrows():
    assert fmt_delta(2500, 2.5) == "▲2,500 (2.50%)"
    assert fmt_delta(-1500, -1.2) == "▼1,500 (1.20%)"
    assert fmt_delta(0) == "•0"
    assert fmt_delta(None) == "n/a"


def test_split_message_short_text_unchanged():
    assert split_message("hello", 100) == ["hello"]


def test_split_message_keeps_pre_blocks_balanced():
    body = "\n".join(f"row {i:03d} " + "x" * 30 for i in range(200))
    text = "<b>Title</b>\n\n" + pre_block(body) + "\n\nfooter"
    chunks = split_message(text, 1000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 1000
        assert chunk.count("<pre>") == chunk.count("</pre>")
    joined = "".join(chunks)
    assert joined.count("row 199") == 1
    assert chunks[-1].endswith("footer")


def test_split_message_handles_single_overlong_line():
    text = "a" * 5000
    chunks = split_message(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == text


def test_split_message_never_cuts_a_card():
    from telegram_bot import card

    cards = [card(n, f"Car {n}", "https://x.y", ["$100,000 · 400 km", "Deposit $40,000"]) for n in range(1, 60)]
    chunks = split_message("<b>Title</b>\n\n" + "\n\n".join(cards), 500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 500
        for block in chunk.split("\n\n")[1 if chunk.startswith("<b>Title") else 0:]:
            assert block.startswith("<b>") and block.endswith("Deposit $40,000"), block


def test_header_and_notes_go_last():
    from telegram_bot import build_section, note

    html = build_section("🎫 COE position", [note("method"), "body"], "23 Sep tender")
    assert html.startswith("🎫 <b>COE POSITION</b> · 23 Sep tender")
    assert html.endswith("<blockquote expandable>method</blockquote>")


def test_rejected_html_is_resent_as_plain_text(monkeypatch):
    from telegram_bot import TelegramClient, TelegramError

    client = TelegramClient("t", "1")
    sent = []

    def post(method, payload):
        if payload.get("parse_mode"):
            raise TelegramError("sendMessage failed: Bad Request: can't parse entities")
        sent.append(payload)
        return {}

    monkeypatch.setattr(client, "_post", post)
    monkeypatch.setattr("telegram_bot.time.sleep", lambda s: None)
    client.send_message('<b>Hi</b> &amp; <a href="https://x.y">go</a>')
    assert sent[0]["text"] == "Hi & go <https://x.y>" and "parse_mode" not in sent[0]
