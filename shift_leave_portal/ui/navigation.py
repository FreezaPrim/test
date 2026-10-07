"""Registry of the current user's navigation pages (filled by ``app.py``).

Pages call :func:`go` to switch pages without hard-coding file paths. Only
pages the current role may access are ever registered.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

PAGES: dict[str, Any] = {}


def go(name: str) -> None:
    """Switch to a registered page if it is available to the current user."""
    page = PAGES.get(name)
    if page is not None:
        st.switch_page(page)
