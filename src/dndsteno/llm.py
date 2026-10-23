"""Client for an OpenAI-compatible chat server (LM Studio, Ollama, llama.cpp server, ...).

Responses are streamed so a cancel takes effect mid-generation and the caller
can show that the model is still producing output.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from typing import Callable

from dndsteno.config import LLMConfig
from dndsteno.errors import Cancelled


class LLMError(Exception):
    pass


def _request(url: str, payload: dict | None, timeout: float):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"Сервер нейросети ответил ошибкой {e.code}: {body}") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise LLMError(
            f"Нет связи с сервером нейросети ({url}): {getattr(e, 'reason', e)}. "
            "Запущен ли сервер в LM Studio (Developer → Start Server)?") from e


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences and chatter around it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"Модель не вернула JSON: {text[:300]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"Модель вернула битый JSON ({e}): {text[start:start + 300]!r}") from e


class LLMClient:
    def __init__(self, cfg: LLMConfig, cancel: threading.Event | None = None):
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self.cancel = cancel or threading.Event()
        self._model = cfg.model or None

    def list_models(self) -> list[str]:
        with _request(f"{self.base_url}/models", None, timeout=15) as resp:
            data = json.load(resp)
        return [m["id"] for m in data.get("data", [])]

    @property
    def model(self) -> str:
        """Configured model, or the first chat model the server reports."""
        if self._model is None:
            models = [m for m in self.list_models() if "embed" not in m.lower()]
            if not models:
                raise LLMError("В LM Studio не загружена ни одна модель. Загрузите модель и повторите.")
            self._model = models[0]
        return self._model

    def chat(
        self,
        messages: list[dict],
        schema: dict | None = None,
        on_text: Callable[[int], None] | None = None,
    ) -> str:
        """Send a chat request; returns the reply text. `on_text(chars_so_far)` is called while streaming."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            # Without a cap a confused model can generate until the context is full (20+ min on CPU).
            "max_tokens": self.cfg.max_tokens,
            "stream": True,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": schema},
            }

        parts: list[str] = []
        received = 0
        finish_reason = None
        with _request(f"{self.base_url}/chat/completions", payload, timeout=self.cfg.timeout) as resp:
            for raw in resp:
                if self.cancel.is_set():
                    raise Cancelled()
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if "error" in chunk:
                    raise LLMError(f"Ошибка нейросети: {chunk['error']}")
                choice = chunk["choices"][0] if chunk.get("choices") else {}
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta", {})
                # Reasoning models stream their thinking separately; count it as progress only.
                thinking = delta.get("reasoning_content") or delta.get("reasoning") or ""
                content = delta.get("content") or ""
                parts.append(content)
                received += len(content) + len(thinking)
                if on_text and (content or thinking):
                    on_text(received)
        if finish_reason == "length":
            raise LLMError(
                f"Ответ нейросети обрезан на лимите {self.cfg.max_tokens} токенов: модель зациклилась "
                "или ей не хватает места. Попробуйте другую модель, уменьшите «Размер куска» "
                "или увеличьте «Макс. длина ответа».")
        return "".join(parts)

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict,
        on_text: Callable[[int], None] | None = None,
        retries: int = 1,
    ) -> dict:
        # One user message instead of system + user: chat templates of some models (gpt-oss in
        # LM Studio) inject their own system prompt, and the model then loses track of ours.
        messages = [{"role": "user", "content": f"{system}\n\n=====\n\n{user}"}]
        last_error: LLMError | None = None
        for _ in range(retries + 1):
            text = self.chat(messages, schema=schema, on_text=on_text)
            try:
                return extract_json(text)
            except LLMError as e:
                last_error = e
        raise last_error
