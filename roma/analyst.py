"""Roma's local learning engine (scikit-learn). No Claude, fully offline.

It learns from your tabular data:
  - drivers:   what most influences a target like CSAT / churn / reopened
  - segments:  natural customer groupings (clustering)
  - trends:    direction of a metric over time
  - anomalies: unusual rows worth a look

Results are saved to roma_data/learned/knowledge.json so Roma "remembers" what
it has learned and updates it whenever you add data or run `roma learn`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from . import config, database

# Column-name hints for likely CX targets, highest priority first.
TARGET_HINTS = [
    "csat", "satisfaction", "nps", "rating", "score", "churn", "churned",
    "cancelled", "canceled", "reopened", "escalated", "complaint", "complaints",
    "resolved", "fcr",
]
ID_LIKE = re.compile(
    r"(?:^|[\W_])(id|msisdn|phone|mobile|account|acct|ticket|case|key|code|"
    r"uuid|guid|no|num|number|sr)(?:$|[\W_])", re.IGNORECASE)
DATE_HINT = re.compile(r"date|time|day|month|created|opened|closed", re.IGNORECASE)


# --------------------------- helpers --------------------------------------- #

def _table_df(conn, table: str) -> pd.DataFrame:
    return pd.read_sql_query(f'SELECT * FROM "{table}"', conn)


def _user_tables(conn) -> list[str]:
    return [t["table"] for t in database.list_tables(conn)]


def _find_target_table(conn, target: str) -> str | None:
    for t in _user_tables(conn):
        cols = [c["name"] for c in next(
            x for x in database.list_tables(conn) if x["table"] == t)["columns"]]
        if target in cols:
            return t
    return None


def infer_target(conn) -> tuple[str | None, str | None]:
    """Guess the most likely target column across all tables."""
    candidates: list[tuple[int, str, str]] = []
    for tinfo in database.list_tables(conn):
        for c in tinfo["columns"]:
            name = c["name"].lower()
            for rank, hint in enumerate(TARGET_HINTS):
                if hint in name:
                    candidates.append((rank, tinfo["table"], c["name"]))
                    break
    if not candidates:
        return None, None
    candidates.sort()
    _, table, col = candidates[0]
    return table, col


# ------------------------- feature assembly -------------------------------- #

def _build_feature_matrix(conn, target_table: str, target: str
                          ) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Join/aggregate other tables onto the target table via shared keys."""
    base = _table_df(conn, target_table)
    notes: list[str] = [f"base table '{target_table}' ({len(base)} rows)"]

    links = database.detect_links(conn)
    base_cols_lower = {c.lower() for c in base.columns}
    joined_tables = 0
    for link in links:
        key = link["column"]
        if key not in base_cols_lower:
            continue
        # real (case-correct) key name in base
        real_key = next(c for c in base.columns if c.lower() == key)
        for other in link["shared_by"]:
            if other == target_table or joined_tables >= config.MAX_JOIN_TABLES:
                continue
            odf = _table_df(conn, other)
            okey = next((c for c in odf.columns if c.lower() == key), None)
            if okey is None:
                continue
            num_cols = [c for c in odf.columns
                        if c != okey and pd.api.types.is_numeric_dtype(odf[c])
                        and not ID_LIKE.search(c)]
            if not num_cols:
                continue
            agg = odf.groupby(okey)[num_cols].mean().add_prefix(f"{other}_")
            base = base.merge(agg, left_on=real_key, right_on=okey, how="left")
            notes.append(f"joined '{other}' on '{key}' (+{len(num_cols)} features)")
            joined_tables += 1

    y = base[target]
    drop = [c for c in base.columns if c == target or ID_LIKE.search(c)
            or DATE_HINT.search(c)]
    X = base.drop(columns=drop, errors="ignore")
    return X, y, notes


def _encode(X: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Numeric passthrough + one-hot for low-cardinality categoricals."""
    parts, used = [], []
    for col in X.columns:
        s = X[col]
        if pd.api.types.is_numeric_dtype(s):
            parts.append(s.fillna(s.median()))
            used.append(col)
        elif s.nunique(dropna=True) <= 20:
            dummies = pd.get_dummies(s.astype("string").fillna("NA"), prefix=col)
            parts.append(dummies)
            used.extend(dummies.columns.tolist())
    if not parts:
        return pd.DataFrame(index=X.index), []
    return pd.concat(parts, axis=1), used


# ------------------------------ analyses ----------------------------------- #

def drivers(conn, target: str | None = None) -> dict[str, Any]:
    if target:
        table = _find_target_table(conn, target)
        if not table:
            return {"error": f"Column '{target}' not found in any table."}
    else:
        table, target = infer_target(conn)
        if not target:
            return {"error": "Couldn't find a likely target column "
                             "(e.g. CSAT, NPS, churn, reopened). "
                             "Specify one, e.g.  roma drivers <column>."}

    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.model_selection import cross_val_score

    X_raw, y, notes = _build_feature_matrix(conn, table, target)
    mask = y.notna()
    X_raw, y = X_raw[mask], y[mask]
    if len(y) < 8:
        return {"error": f"Not enough rows with '{target}' to learn from "
                         f"({len(y)} found; need ~8+)."}

    X, feat_names = _encode(X_raw)
    if X.shape[1] == 0:
        return {"error": "No usable feature columns to learn from."}

    y_num = pd.to_numeric(y, errors="coerce")
    is_numeric = y_num.notna().all()
    # Categorical or binary -> classify; multi-level numeric -> regress (clearer score).
    classify = (not is_numeric) or (y_num.nunique() == 2)

    if classify:
        target_used = y.astype("string")
        model = RandomForestClassifier(n_estimators=200, random_state=0)
        scoring, kind = "accuracy", "classification"
    else:
        target_used = y_num
        model = RandomForestRegressor(n_estimators=200, random_state=0)
        scoring, kind = "r2", "regression"

    folds = max(2, min(5, len(y) // 4))
    if classify:
        min_class = int(target_used.value_counts().min())
        folds = max(2, min(folds, min_class))
    try:
        score = float(np.mean(cross_val_score(model, X, target_used,
                                              cv=folds, scoring=scoring)))
    except Exception:  # noqa: BLE001
        score = float("nan")
    model.fit(X, target_used)

    imp = sorted(zip(feat_names, model.feature_importances_),
                 key=lambda t: t[1], reverse=True)[:10]

    # Direction of effect (numeric target only): sign of correlation.
    directions = {}
    if is_numeric:
        for name, _ in imp:
            if name in X.columns:
                try:
                    r = float(np.corrcoef(X[name], y_num)[0, 1])
                    if not np.isnan(r) and abs(r) > 0.05:
                        directions[name] = ("higher" if r > 0 else "lower")
                except Exception:  # noqa: BLE001
                    pass

    return {
        "target": target, "target_table": table, "model": kind,
        "score": round(score, 3) if score == score else None,
        "score_metric": scoring, "rows_used": int(len(y)),
        "features": [{"feature": n, "importance": round(float(i), 3),
                      "effect": directions.get(n)} for n, i in imp],
        "assembly": notes,
    }


def segments(conn, table: str | None = None, k: int | None = None) -> dict[str, Any]:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    table = table or (infer_target(conn)[0]) or (
        _user_tables(conn)[0] if _user_tables(conn) else None)
    if not table:
        return {"error": "No tables to segment."}
    df = _table_df(conn, table)
    num = df.select_dtypes(include="number")
    num = num[[c for c in num.columns if not ID_LIKE.search(c)]].dropna()
    if num.shape[0] < 6 or num.shape[1] < 1:
        return {"error": f"Table '{table}' has too few numeric values to segment."}

    k = k or max(2, min(4, num.shape[0] // 3))
    Xs = StandardScaler().fit_transform(num)
    labels = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(Xs)
    df_seg = num.copy()
    df_seg["_segment"] = labels
    profiles = []
    for seg in sorted(set(labels)):
        grp = df_seg[df_seg["_segment"] == seg].drop(columns="_segment")
        profiles.append({"segment": int(seg), "size": int(len(grp)),
                         "averages": {c: round(float(grp[c].mean()), 2)
                                      for c in grp.columns}})
    return {"table": table, "k": int(k), "segments": profiles}


def trend(conn, metric: str | None = None) -> dict[str, Any]:
    results = []
    for tinfo in database.list_tables(conn):
        table = tinfo["table"]
        df = _table_df(conn, table)
        date_cols = [c for c in df.columns if DATE_HINT.search(c)]
        num_cols = [c for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c]) and not ID_LIKE.search(c)]
        if metric:
            num_cols = [c for c in num_cols if c.lower() == metric.lower()]
        for dcol in date_cols:
            dt = pd.to_datetime(df[dcol], errors="coerce")
            if dt.notna().sum() < 4:
                continue
            for mcol in num_cols:
                tmp = pd.DataFrame({"d": dt, "m": pd.to_numeric(df[mcol],
                                    errors="coerce")}).dropna().sort_values("d")
                if len(tmp) < 4:
                    continue
                x = (tmp["d"] - tmp["d"].min()).dt.days.to_numpy(dtype=float)
                yv = tmp["m"].to_numpy(dtype=float)
                if np.ptp(x) == 0:
                    continue
                slope = float(np.polyfit(x, yv, 1)[0])
                direction = ("rising" if slope > 0 else
                             "falling" if slope < 0 else "flat")
                results.append({"table": table, "metric": mcol, "over": dcol,
                                "direction": direction,
                                "change_per_day": round(slope, 4),
                                "first": round(float(yv[0]), 2),
                                "last": round(float(yv[-1]), 2)})
    if not results:
        return {"error": "No date+metric combination found to compute a trend."}
    return {"trends": results}


def anomalies(conn, limit: int = 10) -> dict[str, Any]:
    from sklearn.ensemble import IsolationForest
    out = []
    for tinfo in database.list_tables(conn):
        table = tinfo["table"]
        df = _table_df(conn, table)
        num = df.select_dtypes(include="number")
        num = num[[c for c in num.columns if not ID_LIKE.search(c)]].dropna()
        if num.shape[0] < 10 or num.shape[1] < 1:
            continue
        iso = IsolationForest(contamination=0.05, random_state=0)
        flags = iso.fit_predict(num)
        idx = num.index[flags == -1][:limit]
        for i in idx:
            out.append({"table": table, "row": int(i),
                        "values": {c: _jsonable(df.loc[i, c]) for c in num.columns}})
    if not out:
        return {"error": "No numeric outliers detected (or not enough data)."}
    return {"anomalies": out[:limit]}


def _jsonable(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return round(float(v), 3)
    return v


# ------------------------- learn + persist --------------------------------- #

def learn(conn) -> dict[str, Any]:
    """Run every analysis, persist a knowledge snapshot, return it."""
    config.ensure_dirs()
    snapshot = {
        "learned_at": datetime.now().isoformat(timespec="seconds"),
        "tables": [{"table": t["table"], "rows": t["row_count"],
                    "columns": [c["name"] for c in t["columns"]]}
                   for t in database.list_tables(conn)],
        "links": database.detect_links(conn),
        "drivers": drivers(conn),
        "segments": segments(conn),
        "trend": trend(conn),
        "anomalies": anomalies(conn),
        "documents": database.document_sources(conn),
    }
    config.KNOWLEDGE_PATH.write_text(
        json.dumps(snapshot, indent=2, default=_jsonable), encoding="utf-8")
    return snapshot


def load_knowledge() -> dict[str, Any] | None:
    if config.KNOWLEDGE_PATH.exists():
        try:
            return json.loads(config.KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None
