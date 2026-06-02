"""Parse time expressions from a free-form question into a SQL date filter.

Handles: month names (April / أبريل), quarters (Q1), years, 'last month',
'this year', and explicit ranges. Returns a SQL condition string plus a human
label, built against a detected date column. Pure standard library.
"""

from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "january": 1, "jan": 1, "يناير": 1,
    "february": 2, "feb": 2, "فبراير": 2,
    "march": 3, "mar": 3, "مارس": 3,
    "april": 4, "apr": 4, "ابريل": 4, "أبريل": 4, "إبريل": 4,
    "may": 5, "مايو": 5,
    "june": 6, "jun": 6, "يونيو": 6, "يونيه": 6,
    "july": 7, "jul": 7, "يوليو": 7, "يوليه": 7,
    "august": 8, "aug": 8, "اغسطس": 8, "أغسطس": 8,
    "september": 9, "sep": 9, "sept": 9, "سبتمبر": 9,
    "october": 10, "oct": 10, "اكتوبر": 10, "أكتوبر": 10,
    "november": 11, "nov": 11, "نوفمبر": 11,
    "december": 12, "dec": 12, "ديسمبر": 12,
}

DATE_HINT = re.compile(r"date|time|day|month|created|opened|closed|ivr", re.IGNORECASE)


def find_date_column(columns: list[str]) -> str | None:
    """Pick the most likely date column from a list of column names."""
    # Prefer an explicit 'ivr date' style, else anything date-like.
    ranked = sorted(columns, key=lambda c: (0 if "date" in c.lower() else 1,
                                            0 if DATE_HINT.search(c) else 1))
    for c in ranked:
        if DATE_HINT.search(c):
            return c
    return None


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def parse(question: str, date_col: str, today: date | None = None
          ) -> dict | None:
    """Return {'where': sql, 'label': text} or None if no time expression."""
    if not date_col:
        return None
    q = question.lower()
    today = today or date.today()
    col = f'"{date_col}"'
    # normalise the stored value to a date for comparison
    d = f"date({col})"

    # explicit range: 2026-01-01 to 2026-03-31
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|->|\.\.|until|\-)\s*(\d{4}-\d{2}-\d{2})", q)
    if m:
        return {"where": f"{d} BETWEEN date('{m.group(1)}') AND date('{m.group(2)}')",
                "label": f"{m.group(1)} to {m.group(2)}"}

    # year
    ymatch = re.search(r"\b(20\d{2})\b", q)
    year = int(ymatch.group(1)) if ymatch else today.year

    # quarter: Q1..Q4
    qm = re.search(r"\bq([1-4])\b", q)
    if qm:
        qn = int(qm.group(1))
        start_month = (qn - 1) * 3 + 1
        s, _ = _month_bounds(year, start_month)
        _, e = _month_bounds(year, start_month + 2)
        return {"where": f"{d} >= date('{s}') AND {d} < date('{e}')",
                "label": f"Q{qn} {year}"}

    # month name
    for name, mn in MONTHS.items():
        if re.search(rf"\b{name}\b", q):
            s, e = _month_bounds(year, mn)
            return {"where": f"{d} >= date('{s}') AND {d} < date('{e}')",
                    "label": f"{name.capitalize()} {year}"}

    # relative: last month / this month / last 30 days / this year
    if re.search(r"last month|الشهر اللي فات|الشهر الماضي|اخر شهر|آخر شهر", q):
        m0 = today.month - 1 or 12
        y0 = today.year - 1 if today.month == 1 else today.year
        s, e = _month_bounds(y0, m0)
        return {"where": f"{d} >= date('{s}') AND {d} < date('{e}')",
                "label": f"last month ({s[:7]})"}
    if re.search(r"this month|الشهر ده|الشهر الحالي|هذا الشهر", q):
        s, e = _month_bounds(today.year, today.month)
        return {"where": f"{d} >= date('{s}') AND {d} < date('{e}')",
                "label": f"this month ({s[:7]})"}
    if re.search(r"last 30 days|اخر 30 يوم|آخر ٣٠", q):
        return {"where": f"{d} >= date('now','-30 days')", "label": "last 30 days"}
    if re.search(r"this year|السنة دي|هذا العام|العام ده", q):
        return {"where": f"strftime('%Y',{col}) = '{today.year}'",
                "label": f"{today.year}"}

    return None
