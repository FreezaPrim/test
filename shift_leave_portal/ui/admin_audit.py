"""Admin page: read-only audit log viewer."""

from __future__ import annotations

import json
import math
from datetime import timedelta

import pandas as pd
import streamlit as st

from services import audit_service
from services.audit_service import AuditAction
from ui.components import page_header, require_admin_user, run_service
from utils.datetime_utils import now_utc, to_local

ACTIONS = sorted(v for k, v in vars(AuditAction).items() if k.isupper())
PERIODS = {1: "Last 24 hours", 7: "Last 7 days", 30: "Last 30 days", 365: "Last 12 months"}
PAGE_SIZE = 100


def render() -> None:
    """Page: audit log."""
    user = require_admin_user()
    page_header("Audit log", "Every sign-in, change, decision and export is recorded here.")
    c1, c2, c3 = st.columns([2, 1, 1])
    actions = c1.multiselect("Actions", ACTIONS, format_func=lambda a: a.replace("_", " ").capitalize())
    entity = c2.selectbox("Record type", ["", "submission", "user", "attachment"],
                          format_func=lambda e: e.capitalize() if e else "All")
    days = c3.selectbox("Period", list(PERIODS), index=1, format_func=PERIODS.get)
    page = c3.number_input("Page", min_value=1, value=1, step=1)

    result = run_service(
        lambda s: audit_service.list_audit_logs(
            s, user, actions=actions or None, entity_type=entity or None,
            since=now_utc() - timedelta(days=days), limit=PAGE_SIZE, offset=(int(page) - 1) * PAGE_SIZE,
        )
    )
    if result is None:
        return
    rows, total = result
    st.caption(f"{total} entr{'y' if total == 1 else 'ies'} · page {int(page)} of {max(1, math.ceil(total / PAGE_SIZE))}")
    if not rows:
        st.info("No audit entries for these filters.")
        return
    df = pd.DataFrame(
        [
            {
                "When": to_local(r.created_at).replace(tzinfo=None),  # type: ignore[union-attr]
                "Actor": r.actor,
                "Action": r.action,
                "Record": f"{r.entity_type} #{r.entity_id}" if r.entity_id else r.entity_type,
                "Before": json.dumps(r.old_values, ensure_ascii=False) if r.old_values else "",
                "After": json.dumps(r.new_values, ensure_ascii=False) if r.new_values else "",
                "IP": r.ip_address or "",
            }
            for r in rows
        ]
    )
    st.dataframe(
        df, hide_index=True, width="stretch", height=560,
        column_config={"When": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm:ss")},
    )
