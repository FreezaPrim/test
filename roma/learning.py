"""Roma's learning store: remembers what Mohamed teaches it.

Two kinds of learning, saved to roma_data/learned/teachings.json so they persist
across sessions:

  - phrase_intent: a whole phrase (or distinctive words) maps to an intent,
    e.g. "unhappy people" -> detractor_by. Added when you correct Roma.
  - word_meaning:  a single word maps to a canonical token the NLU understands,
    e.g. "happiness" -> csat. Added when you teach a synonym.

Every time Roma answers, it can learn the LAST question's mapping if you correct
it. This is real learning from your phrasing - no model required.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import config

STORE = config.LEARNED_DIR / "teachings.json"

VALID_INTENTS = {
    "drives", "detractor_by", "segment", "trend", "anomaly", "repeat", "top",
    "metric_by", "kpi", "summary", "identity", "stats", "export",
}


def _load() -> dict[str, Any]:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {"phrase_intent": {}, "word_meaning": {}}


def _save(data: dict) -> None:
    config.ensure_dirs()
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def teach_intent(phrase: str, intent: str) -> bool:
    intent = intent.strip().lower()
    if intent not in VALID_INTENTS:
        return False
    data = _load()
    data.setdefault("phrase_intent", {})[_key(phrase)] = intent
    _save(data)
    return True


def teach_word(word: str, meaning: str) -> None:
    data = _load()
    data.setdefault("word_meaning", {})[word.lower().strip()] = meaning.lower().strip()
    _save(data)


def learned_intent(question: str) -> str | None:
    """Return a taught intent if the question matches a learned phrase."""
    data = _load()
    q = _key(question)
    phrases = data.get("phrase_intent", {})
    # exact, then containment (taught phrase appears inside the question)
    if q in phrases:
        return phrases[q]
    for phrase, intent in phrases.items():
        if phrase and phrase in q:
            return intent
    return None


def learned_words() -> dict[str, str]:
    return _load().get("word_meaning", {})


def all_teachings() -> dict[str, Any]:
    return _load()


def forget_all() -> None:
    _save({"phrase_intent": {}, "word_meaning": {}})
