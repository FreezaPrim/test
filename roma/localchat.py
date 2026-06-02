"""Optional local natural-language chat via Ollama (http://localhost:11434).

This is the "add local chat if my laptop allows" piece. If Ollama is installed
and running with a model pulled, Roma uses it to answer free-form questions in
natural language, grounded in what the analyst has learned. If Ollama isn't
there, everything else still works - Roma just answers in structured form.

Uses only the Python standard library so it adds no dependencies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config


def available() -> tuple[bool, str | None]:
    """Return (is_available, model_name). Never raises."""
    try:
        req = urllib.request.Request(f"{config.OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.load(r)
        models = [m.get("name", "") for m in data.get("models", [])]
        if not models:
            return False, None
        if config.OLLAMA_MODEL and config.OLLAMA_MODEL in models:
            return True, config.OLLAMA_MODEL
        return True, (config.OLLAMA_MODEL or models[0])
    except Exception:  # noqa: BLE001 - any failure just means "not available"
        return False, None


def chat(model: str, system: str, user: str, timeout: int = 120) -> str:
    """Single-shot chat completion against a local Ollama model."""
    body = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        f"{config.OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return (data.get("message", {}) or {}).get("content", "").strip()
