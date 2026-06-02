"""Free-form question understanding for Roma (pure standard library).

Inspired by ln2sql (lexicon-driven NL->SQL) and in-context matching ideas from
open text-to-SQL projects, but written from scratch with zero new dependencies
so it runs on a locked-down laptop with no model and no internet.

The flow:
  1. Normalise the question (lowercase, light Arabic normalisation, synonyms).
  2. Score it against a set of INTENTS by keyword overlap.
  3. Resolve any column/value names it mentions - exact, then fuzzy.
  4. Hand back a structured plan the engine turns into SQL/analysis.

If a local model (Ollama) is present, the engine still prefers it; this layer is
what makes Roma genuinely understand free phrasing when no model is available.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

# ---- light normalisation -------------------------------------------------- #

_AR_MAP = str.maketrans("أإآىئءؤة", "ااايياوه")

# Words that mean the same thing, folded to a canonical token.
_SYNONYMS = {
    "driver": "drives", "drivers": "drives", "influence": "drives",
    "influences": "drives", "affect": "drives", "affects": "drives",
    "impact": "drives", "cause": "drives", "causes": "drives",
    "factor": "drives", "factors": "drives", "why": "drives",
    "makes": "drives", "make": "drives", "reason": "drives",
    "reasons": "drives", "behind": "drives", "main": "drives",
    "mainly": "drives", "determines": "drives", "predict": "drives",
    "unhappy": "detractor", "unhappiest": "detractor", "dissatisfied": "detractor",
    "angry": "detractor", "complaints": "detractor",
    "avg": "average", "mean": "average",
    "count": "count", "many": "count", "number": "count",
    "breakdown": "by", "per": "by", "across": "by", "split": "by",
    "repeat": "repeat", "repeated": "repeat", "recurring": "repeat",
    "again": "repeat", "multiple": "repeat",
    "top": "top", "most": "top", "highest": "top", "biggest": "top",
    "leading": "top", "common": "top", "frequent": "top",
    "lowest": "bottom", "worst": "bottom", "least": "bottom",
    "anomaly": "anomaly", "anomalies": "anomaly", "outlier": "anomaly",
    "outliers": "anomaly", "unusual": "anomaly", "weird": "anomaly",
    "segment": "segment", "segments": "segment", "cluster": "segment",
    "clusters": "segment", "group": "segment", "groups": "segment",
    "persona": "segment", "tier": "segment",
    "trend": "trend", "trends": "trend", "over": "trend",
    "rising": "trend", "falling": "trend", "growth": "trend",
    "kpi": "kpi", "kpis": "kpi", "metrics": "kpi",
    "detractor": "detractor", "detractors": "detractor",
    "promoter": "promoter", "promoters": "promoter",
    "nps": "nps", "tnps": "nps",
    "csat": "csat", "satisfaction": "csat",
    "reach": "reach", "reached": "reach",
    "contact": "contact", "contacted": "contact",
    "customer": "customer", "customers": "customer", "caller": "customer",
    "callers": "customer", "client": "customer", "clients": "customer",
    "you": "you", "yourself": "you", "your": "you", "roma": "you",
    "help": "help", "do": "do", "can": "can",
    "summary": "summary", "overview": "summary", "know": "summary",
    "show": "show", "list": "show", "give": "show", "tell": "show",
    # --- Arabic CX vocabulary (keys are POST-normalisation forms) ---
    "بياثر": "drives", "يوثر": "drives", "توثر": "drives", "سبب": "drives",
    "اسباب": "drives", "ليه": "drives", "بيخلي": "drives", "العامل": "drives",
    "العوامل": "drives", "بيحرك": "drives", "يحرك": "drives",
    "الفيات": "segment", "فيات": "segment", "شرايح": "segment",
    "مجموعات": "segment", "تصنيفات": "segment", "الشرايح": "segment",
    "اتجاه": "trend", "ترند": "trend", "بمرور": "trend",
    "شاذ": "anomaly", "شاذه": "anomaly", "غريب": "anomaly", "غريبه": "anomaly",
    "مكرر": "repeat", "مكررين": "repeat", "متكرر": "repeat",
    "المكررين": "repeat", "المتكررين": "repeat",
    "عميل": "customer", "عملاء": "customer", "العملاا": "customer",
    "اعلي": "top", "اكتر": "top", "اكثر": "top",
    "ملخص": "summary", "نظره": "summary", "تعرف": "summary", "البيانات": "summary",
    "منتقد": "detractor", "منتقدين": "detractor", "المنتقدين": "detractor",
    "غاضب": "detractor",
    "حسب": "by", "لكل": "by",
}

# Intent -> the canonical tokens that signal it, and a priority (lower first).
INTENTS = [
    ("identity",   {"you"}, 0),
    ("summary",    {"summary"}, 1),
    ("detractor_by", {"detractor"}, 2),
    ("drives",     {"drives"}, 2),
    ("segment",    {"segment"}, 3),
    ("trend",      {"trend"}, 3),
    ("anomaly",    {"anomaly"}, 3),
    ("repeat",     {"repeat"}, 3),
    ("top",        {"top", "by"}, 4),
    ("metric_by",  {"average", "by"}, 4),
    ("kpi",        {"kpi", "nps", "csat", "detractor", "reach", "contact",
                    "promoter"}, 6),
]


def normalise(text: str) -> list[str]:
    t = text.lower().translate(_AR_MAP)
    raw = re.findall(r"[a-z_0-9\u0600-\u06ff]+", t)
    try:
        from . import learning
        learned = learning.learned_words()
    except Exception:  # noqa: BLE001
        learned = {}
    out = []
    for w in raw:
        w = learned.get(w, w)          # learned synonyms first
        out.append(_SYNONYMS.get(w, w))  # then built-in synonyms
    return out


def _all_signal_words() -> dict[str, str]:
    mapping = {}
    for name, signals, _ in INTENTS:
        for s in signals:
            mapping[s] = name
    return mapping


def score_intents(tokens: list[str]) -> list[tuple[str, float, int]]:
    tset = set(tokens)
    scored = []
    for name, signals, prio in INTENTS:
        overlap = len(tset & signals)
        if overlap:
            scored.append((name, overlap / len(signals), prio))
    # Fuzzy fallback: nothing matched exactly -> closest signal word (typos/
    # near-synonyms) so Roma still understands without a model.
    if not scored:
        signal_map = _all_signal_words()
        for tok in tokens:
            if len(tok) < 4:
                continue
            m = difflib.get_close_matches(tok, list(signal_map), n=1, cutoff=0.8)
            if m:
                scored.append((signal_map[m[0]], 0.3, 5))
                break
    scored.sort(key=lambda x: (-x[1], x[2]))
    return scored


def resolve_column(tokens: list[str], columns: dict[str, str]) -> str | None:
    """columns: lower_name -> real_name. Exact token, then fuzzy, then bigram."""
    for tok in tokens:
        if tok in columns:
            return columns[tok]
    lower_names = list(columns.keys())
    # bigrams: "owner team" -> "owner_team" / "ownerteam"
    for a, b in zip(tokens, tokens[1:]):
        for joined in (f"{a}_{b}", f"{a}{b}"):
            if joined in columns:
                return columns[joined]
            match = difflib.get_close_matches(joined, lower_names, n=1, cutoff=0.82)
            if match:
                return columns[match[0]]
    # single-token fuzzy
    for tok in tokens:
        if len(tok) < 3:
            continue
        match = difflib.get_close_matches(tok, lower_names, n=1, cutoff=0.82)
        if match:
            return columns[match[0]]
    # token contained in a column name (e.g. "team" in "owner_team")
    for tok in tokens:
        if len(tok) < 4:
            continue
        for low, real in columns.items():
            if tok in low.split("_") or tok in low:
                return real
    return None


def resolve_dimension(tokens: list[str], columns: dict[str, str]) -> str | None:
    """The column that comes after 'by'."""
    if "by" in tokens:
        idx = tokens.index("by")
        after = tokens[idx + 1:]
        return resolve_column(after, columns)
    return None


def understand(question: str, columns: dict[str, str]) -> dict[str, Any]:
    """Return a plan: {intent, column, dimension, tokens, confidence}."""
    tokens = normalise(question)
    intents = score_intents(tokens)
    intent, confidence = (intents[0][0], intents[0][1]) if intents else (None, 0.0)
    return {
        "intent": intent,
        "confidence": confidence,
        "tokens": tokens,
        "column": resolve_column(tokens, columns),
        "dimension": resolve_dimension(tokens, columns),
        "candidates": [i[0] for i in intents[:3]],
    }
