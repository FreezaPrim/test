"""Compatibility shim: delegates to roma.llm.

All new code should import from roma.llm directly.
This module is kept so existing callers (engine.py, cli.py) don't break
during the transition to the multi-engine llm.py layer.
"""

from __future__ import annotations

from .llm import OllamaEngine, auto_engine
from . import config


def available() -> tuple[bool, str | None]:
    """Return (is_available, model_name). Never raises.

    Checks Ollama first (matching original behaviour), then any other
    engine discovered by llm.auto_engine().
    """
    try:
        eng = OllamaEngine()
        models = eng.list_models()
        if models:
            if config.OLLAMA_MODEL and config.OLLAMA_MODEL in models:
                return True, config.OLLAMA_MODEL
            from .llm import _best_model
            return True, _best_model(models)
    except Exception:  # noqa: BLE001
        pass

    # fall back to auto-discovery (LM Studio, llama.cpp, vLLM, …)
    try:
        eng, model = auto_engine()
        if eng and model:
            return True, model
    except Exception:  # noqa: BLE001
        pass

    return False, None


def chat(model: str, system: str, user: str, timeout: int = 120) -> str:
    """Single-shot chat completion via the best available local engine."""
    # Try Ollama first (same default host as before)
    try:
        eng = OllamaEngine()
        msgs = [{"role": "system", "content": system},
                {"role": "user",   "content": user}]
        return eng.generate(msgs, model, timeout=timeout)
    except Exception:  # noqa: BLE001
        pass

    # Fallback: auto-discovered engine
    try:
        eng, discovered_model = auto_engine()
        if eng:
            use_model = model or discovered_model or ""
            msgs = [{"role": "system", "content": system},
                    {"role": "user",   "content": user}]
            return eng.generate(msgs, use_model)
    except Exception:  # noqa: BLE001
        pass

    raise RuntimeError("No local LLM available (Ollama / LM Studio / llama.cpp / vLLM)")
