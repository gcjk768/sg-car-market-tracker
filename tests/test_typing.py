"""The ask command shows 'typing...' while the model works, then stops."""
import time

from telegram_bot import TelegramClient


class FakeHttp:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))


def test_typing_sends_chat_action_then_stops():
    c = TelegramClient("123:abc", "-100")
    c.http, c.thread_id = FakeHttp(), 7
    with c.typing(every=0.05):
        time.sleep(0.3)
    n = len(c.http.calls)
    time.sleep(0.2)
    assert n >= 2
    url, payload = c.http.calls[0]
    assert url.endswith("/sendChatAction") and payload["action"] == "typing" and payload["message_thread_id"] == 7
    assert len(c.http.calls) == n
