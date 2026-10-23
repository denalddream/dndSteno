"""LLM client against a tiny fake OpenAI-compatible streaming server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dndsteno.config import LLMConfig
from dndsteno.errors import Cancelled
from dndsteno.llm import LLMClient, LLMError, extract_json


class FakeServer:
    def __init__(self, pieces: list[str], finish: str = "stop"):
        self.pieces, self.finish, self.requests = pieces, finish, []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({"data": [{"id": "text-embedding-x"}, {"id": "fake-chat"}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for i, p in enumerate(server.pieces):
                    last = i == len(server.pieces) - 1
                    chunk = {"choices": [{"delta": {"content": p}, "finish_reason": server.finish if last else None}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server_factory():
    servers = []

    def make(*args, **kwargs):
        s = FakeServer(*args, **kwargs)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def test_streams_json_and_picks_chat_model(server_factory):
    srv = server_factory(['{"a": ', '1, "b": "привет"}'])
    client = LLMClient(LLMConfig(base_url=srv.url))
    seen = []
    assert client.chat_json("инструкция", "текст", {"type": "object"}, on_text=seen.append) == {"a": 1, "b": "привет"}
    req = srv.requests[0]
    assert req["model"] == "fake-chat"                 # embedding model skipped
    assert [m["role"] for m in req["messages"]] == ["user"]  # instructions folded into one message
    assert "инструкция" in req["messages"][0]["content"] and "текст" in req["messages"][0]["content"]
    assert req["max_tokens"] == LLMConfig().max_tokens
    assert seen and seen[-1] == len('{"a": 1, "b": "привет"}')


def test_truncated_answer_is_an_error(server_factory):
    srv = server_factory(['{"a": '], finish="length")
    with pytest.raises(LLMError, match="обрезан"):
        LLMClient(LLMConfig(base_url=srv.url, model="m")).chat_json("s", "u", {}, retries=0)


def test_cancel_stops_stream(server_factory):
    srv = server_factory(["{}"])
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        LLMClient(LLMConfig(base_url=srv.url, model="m"), cancel=cancel).chat([{"role": "user", "content": "x"}])


def test_unreachable_server_has_helpful_message():
    with pytest.raises(LLMError, match="LM Studio"):
        LLMClient(LLMConfig(base_url="http://127.0.0.1:9/v1", model="m", timeout=2)).chat([])


def test_extract_json_tolerates_wrapping():
    assert extract_json('Вот ответ:\n```json\n{"x": [1]}\n```') == {"x": [1]}
    assert extract_json('<think>хм {нет}</think>{"ok": true}') == {"ok": True}
    with pytest.raises(LLMError):
        extract_json("никакого json")
