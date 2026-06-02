"""Configuration for Roma (fully local).

Roma runs offline. The only optional online-ish piece is a *local* chat model
served by Ollama on this same machine; if it isn't there, Roma still works as a
learning analyst. Settings come from environment variables, optionally loaded
from a `.env` file next to the project.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "roma_data"
DB_PATH = DATA_DIR / "roma.db"
REPORTS_DIR = DATA_DIR / "reports"
LEARNED_DIR = DATA_DIR / "learned"
KNOWLEDGE_PATH = LEARNED_DIR / "knowledge.json"


def _load_dotenv() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# --- Optional local chat model (Ollama). Leave defaults if unused. ---
# Roma auto-detects Ollama at this URL. If reachable, it uses a local model for
# natural-language chat. If not, it falls back to the structured analyst.
OLLAMA_URL = os.environ.get("ROMA_OLLAMA_URL", "http://localhost:11434").strip()
# Which local model to use. Empty = auto-pick the first model Ollama has.
OLLAMA_MODEL = os.environ.get("ROMA_OLLAMA_MODEL", "").strip()

# How many shared-key tables to fold into the learning feature matrix.
MAX_JOIN_TABLES = int(os.environ.get("ROMA_MAX_JOIN_TABLES", "6"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, REPORTS_DIR, LEARNED_DIR):
        d.mkdir(parents=True, exist_ok=True)
