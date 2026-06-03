"""Multi-engine LLM backend for Roma.

Architecture inspired by OpenJarvis's InferenceEngine abstraction
(src/openjarvis/engine/_stubs.py + engine/ollama.py + engine/_discovery.py).

Supports, in probe order:
  1. Ollama          — http://localhost:11434   (default, best for most users)
  2. LM Studio       — http://localhost:1234    (OpenAI-compat)
  3. llama.cpp srv   — http://localhost:8080    (OpenAI-compat)
  4. vLLM / SGLang   — http://localhost:8000    (OpenAI-compat)
  5. Custom host     — ROMA_LLM_HOST env var    (any OpenAI-compat server)

All engines use only the Python standard library — no httpx/openai dep.
localchat.py stays as a compatibility shim that delegates here.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from . import config


# ── Abstract base ──────────────────────────────────────────────────────────

class LLMEngine(ABC):
    """Minimal sync LLM interface for Roma (mirrors OpenJarvis InferenceEngine)."""

    engine_id: str = "base"

    @abstractmethod
    def generate(self, messages: list[dict[str, Any]], model: str, **kw: Any) -> str:
        """Return the assistant's reply for the given message list."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return available model names (empty list = engine unreachable)."""

    def available(self) -> bool:
        try:
            return bool(self.list_models())
        except Exception:   # noqa: BLE001
            return False


# ── Ollama ─────────────────────────────────────────────────────────────────

class OllamaEngine(LLMEngine):
    """Ollama backend — http://localhost:11434 by default."""

    engine_id = "ollama"

    def __init__(self, host: str | None = None) -> None:
        self._host = (
            host
            or os.environ.get("OLLAMA_HOST")
            or config.OLLAMA_URL
        ).rstrip("/")

    def list_models(self) -> list[str]:
        req = urllib.request.Request(f"{self._host}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]

    def generate(self, messages: list[dict[str, Any]], model: str, **kw: Any) -> str:
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": kw.get("temperature", 0.3),
                "num_predict": kw.get("max_tokens", 1024),
                "num_ctx": kw.get("num_ctx", 8192),
            },
        }).encode()
        req = urllib.request.Request(
            f"{self._host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return (data.get("message") or {}).get("content", "").strip()


# ── OpenAI-compatible (LM Studio, llama.cpp, vLLM, SGLang, Nexa, …) ───────

class OpenAICompatEngine(LLMEngine):
    """Any server exposing POST /v1/chat/completions (OpenAI format).

    Works with: LM Studio (1234), llama.cpp server (8080),
    vLLM (8000), Nexa, Jan, Open WebUI proxy, etc.
    """

    engine_id = "openai_compat"

    def __init__(self, host: str, api_key: str = "local") -> None:
        self._host = host.rstrip("/")
        self._api_key = api_key

    def list_models(self) -> list[str]:
        req = urllib.request.Request(
            f"{self._host}/v1/models",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
        return [m["id"] for m in data.get("data", [])]

    def generate(self, messages: list[dict[str, Any]], model: str, **kw: Any) -> str:
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": kw.get("temperature", 0.3),
            "max_tokens": kw.get("max_tokens", 1024),
        }).encode()
        req = urllib.request.Request(
            f"{self._host}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()


# ── Auto-discovery (mirrors OpenJarvis _discovery.py probe loop) ───────────

_PROBE_CANDIDATES = [
    ("ollama",    lambda: OllamaEngine()),
    ("lmstudio",  lambda: OpenAICompatEngine("http://localhost:1234")),
    ("llamacpp",  lambda: OpenAICompatEngine("http://localhost:8080")),
    ("vllm",      lambda: OpenAICompatEngine("http://localhost:8000")),
]


def _best_model(models: list[str]) -> str:
    """Prefer a capable chat model; deprioritise embeddings and huge weights."""
    def _rank(m: str) -> int:
        m = m.lower()
        if any(x in m for x in ("embed", "clip", "vision", "whisper")): return 99
        if any(x in m for x in ("70b", "72b", "34b", "32b")):           return 0
        if any(x in m for x in ("13b", "14b", "8b", "7b")):             return 1
        if any(x in m for x in ("3b", "4b", "phi", "gemma")):           return 2
        return 5
    return min(models, key=_rank)


def auto_engine() -> tuple[LLMEngine, str] | tuple[None, None]:
    """Probe engines in priority order.

    Returns (engine_instance, best_model_name) or (None, None) if nothing
    is reachable. Respects ROMA_LLM_HOST env var for custom endpoints.
    """
    # env-var override takes highest priority
    custom_host = os.environ.get("ROMA_LLM_HOST")
    if custom_host:
        try:
            eng = OpenAICompatEngine(custom_host)
            models = eng.list_models()
            if models:
                return eng, _best_model(models)
        except Exception:   # noqa: BLE001
            pass

    for _name, factory in _PROBE_CANDIDATES:
        try:
            eng = factory()
            models = eng.list_models()
            if models:
                # respect OLLAMA_MODEL config preference
                if (isinstance(eng, OllamaEngine)
                        and config.OLLAMA_MODEL
                        and config.OLLAMA_MODEL in models):
                    return eng, config.OLLAMA_MODEL
                return eng, _best_model(models)
        except Exception:   # noqa: BLE001
            continue

    return None, None


def engine_info(eng: LLMEngine | None) -> str:
    """Human-readable one-liner describing the active engine."""
    if eng is None:
        return "no local LLM detected"
    hosts = {
        "ollama":       "Ollama",
        "openai_compat": getattr(eng, "_host", "OpenAI-compat"),
    }
    return hosts.get(eng.engine_id, eng.engine_id)
