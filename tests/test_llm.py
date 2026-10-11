import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from flow.cleanup import CleanupChain
from flow.cleanup.llm import ClaudeCleaner, LocalLlmCleaner, accept_output, chunk_text
from flow.cleanup.rules import RuleCleaner


def test_guard_rejects_chatty_or_empty_output():
    src = "what time is the meeting tomorrow"
    assert accept_output(src, "What time is the meeting tomorrow?")
    assert not accept_output(src, "")
    assert not accept_output(src, "Sure! Here's the cleaned text: What time is it?")
    assert not accept_output(src, "The meeting is at 10am tomorrow in the main conference room "
                                  "on the third floor, and lunch will be provided afterwards for all.")
    assert not accept_output("one two three four five six seven eight nine ten", "One.")


def test_chunking_preserves_all_text():
    text = " ".join(f"Sentence number {i}." for i in range(1000))
    chunks = chunk_text(text, 50)
    assert len(chunks) > 10
    assert all(len(c.split()) <= 50 for c in chunks)
    assert " ".join(chunks) == text


class FakeAnthropic:
    def __init__(self, reply, stop_reason="end_turn"):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self.reply = reply
        self.stop_reason = stop_reason

    def _create(self, **kw):
        self.calls.append(kw)
        content = [SimpleNamespace(type="text", text=self.reply)]
        return SimpleNamespace(content=content, stop_reason=self.stop_reason)


def test_claude_cleaner_request_shape():
    client = FakeAnthropic("Let's meet at 4pm.")
    cleaner = ClaudeCleaner(client=client, vocabulary=["Kubernetes"])
    assert cleaner.clean("lets meet at 3 no wait 4pm") == "Let's meet at 4pm."
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "<transcript>" in call["messages"][0]["content"]
    assert "Kubernetes" in call["messages"][0]["content"]


def test_claude_truncation_raises():
    cleaner = ClaudeCleaner(client=FakeAnthropic("partial", stop_reason="max_tokens"))
    with pytest.raises(RuntimeError):
        cleaner.clean("some text here")


class BrokenCleaner:
    name = "broken"

    def clean(self, text):
        raise ConnectionError("server down")


def test_chain_falls_back_to_rules_on_llm_failure():
    chain = CleanupChain(RuleCleaner(), BrokenCleaner(), min_words_for_llm=1)
    assert chain.clean("um hello there world") == "Hello there world"


def test_chain_skips_llm_for_short_text():
    chain = CleanupChain(RuleCleaner(), BrokenCleaner(), min_words_for_llm=4)
    assert chain.clean("yes") == "Yes"
    assert "broken" not in chain.last_timings


@pytest.fixture
def openai_stub():
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.update(body)
            out = {"choices": [{"message": {"content": "Hello, world."}, "finish_reason": "stop"}]}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", received
    server.shutdown()


def test_local_cleaner_openai_compatible(openai_stub):
    url, received = openai_stub
    cleaner = LocalLlmCleaner(url, "test-model")
    assert cleaner.clean("hello world") == "Hello, world."
    assert received["model"] == "test-model"
    assert received["messages"][0]["role"] == "system"
