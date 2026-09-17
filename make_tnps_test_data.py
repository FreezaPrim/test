"""Synthetic IVR survey data that exercises every rule in the analyser.

Deliberately included:
  - a weekly volume cycle (weekends ~45% of a weekday), so the same-weekday
    baseline has something to correct for
  - firing violations: cooldown breaches, over-cap trials, out-of-window fires
  - a genuine tNPS LEVEL SHIFT mid-February, for the changepoint detector
  - a mix shift between months, so the MoM bridge has both mix and rate to split
  - messy headers, duplicate mapping keys, an unmapped short code, an
    instructions tab before the real mapping, out-of-range scores
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

RNG = np.random.default_rng(20260217)
HERE = Path(__file__).resolve().parent

WEEKDAY_FACTOR = {0: 1.05, 1: 1.05, 2: 1.00, 3: 1.00, 4: 0.95, 5: 0.45, 6: 0.40}

QUEUES = [
    ("Consumer Care",     "CARE",  "Consumer"),
    ("Enterprise Sales",  "SALES", "Enterprise"),
    ("Internet ADSL",     "TECH",  "Internet"),
    ("Retention Desk",    "CARE",  "Consumer"),
    ("NON_TELECOM Desk",  "PART",  "Non Telecom"),
]

# short code -> (weight, base detractor rate, base promoter rate)
SHORT_CODES = {
    "SC101": (0.22, 0.18, 0.55),   # billing query
    "SC102": (0.18, 0.22, 0.48),   # network issue
    "SC103": (0.15, 0.12, 0.62),   # package change
    "SC104": (0.12, 0.35, 0.34),   # complaint
    "SC105": (0.10, 0.15, 0.58),   # activation
    "SC106": (0.09, 0.28, 0.40),   # fault repeat
    "SC107": (0.08, 0.10, 0.66),   # info request
    "SC108": (0.06, 0.45, 0.25),   # escalation  <- degrades mid-Feb
}
DEGRADING_CODE = "SC108"
SHIFT_DATE = dt.date(2026, 2, 14)

# Answer probability by attempt: later attempts convert far less well, which is
# what the trial-effectiveness chart exists to reveal.
ANSWER_P = {1: 0.38, 2: 0.24, 3: 0.15, 4: 0.10, 5: 0.08}


def score_for(code: str, when: dt.date) -> int:
    det, prom = SHORT_CODES[code][1], SHORT_CODES[code][2]
    if code == DEGRADING_CODE and when >= SHIFT_DATE:
        det, prom = min(det + 0.30, 0.92), max(prom - 0.22, 0.03)
    roll = RNG.random()
    if roll < det:
        return int(RNG.integers(0, 7))
    if roll < det + (1 - det - prom):
        return int(RNG.integers(7, 9))
    return int(RNG.integers(9, 11))


def fire_time(day: dt.date, in_window: bool = True) -> dt.datetime:
    if in_window:
        hour = int(RNG.choice(range(10, 21), p=_hour_weights()))
    else:
        hour = int(RNG.choice([7, 8, 9, 22, 23]))
    return dt.datetime.combine(day, dt.time(hour, int(RNG.integers(0, 60)),
                                            int(RNG.integers(0, 60))))


def _hour_weights() -> np.ndarray:
    # Busier late morning and early evening, quieter over lunch.
    raw = np.array([1.0, 1.3, 1.4, 1.1, 0.9, 1.0, 1.2, 1.3, 1.2, 1.0, 0.7])
    return raw / raw.sum()


def build() -> pd.DataFrame:
    days = pd.date_range("2026-01-05", "2026-02-27", freq="D").date
    rows: list[dict] = []
    interaction = 700_000
    subscriber_seq = 0
    answered_log: dict[str, dt.datetime] = {}

    for day in days:
        base_events = 120 * WEEKDAY_FACTOR[day.weekday()]
        # February runs a bit heavier, and tilts toward the escalation code,
        # so the MoM bridge has a genuine MIX component as well as a RATE one.
        if day.month == 2:
            base_events *= 1.12
        n_events = int(RNG.normal(base_events, base_events * 0.08))

        codes = list(SHORT_CODES)
        weights = np.array([SHORT_CODES[c][0] for c in codes], dtype=float)
        if day.month == 2:
            weights[codes.index(DEGRADING_CODE)] *= 2.2      # the mix shift
        weights = weights / weights.sum()

        for _ in range(max(n_events, 0)):
            subscriber_seq += 1
            msisdn = f"01{RNG.integers(0, 5)}{RNG.integers(10_000_000, 99_999_999)}"
            code = str(RNG.choice(codes, p=weights))
            queue, prefix, _seg = QUEUES[int(RNG.integers(0, len(QUEUES)))]
            sr_digits = 12 if RNG.random() > 0.35 else 8
            sr_number = "".join(str(int(RNG.integers(0, 10))) for _ in range(sr_digits))

            clock = fire_time(day)
            for attempt in range(1, 4):
                interaction += 1
                answered = RNG.random() < ANSWER_P[attempt]
                score = score_for(code, day) if answered else None
                rows.append({
                    "MSISDN": msisdn,
                    "OUTBOUND_IVR_DT": clock,
                    "Q1_ANSWER: TNPS": score,
                    "SURVEY_COMPLETION_FLAG": ("Y" if answered and RNG.random() > 0.18
                                               else "N"),
                    "SHORT_CODE": code,
                    "AGENT_QUEUE": queue,
                    "SR NUMBER": sr_number,
                    "INTERACTION_ID": f"I{interaction:09d}",
                })
                if answered:
                    answered_log[msisdn] = clock
                    break
                # Retry 4-26 hours later, still inside the window.
                nxt = clock + dt.timedelta(hours=float(RNG.uniform(4, 26)))
                clock = fire_time(nxt.date())
                if clock.date() > days[-1]:
                    break

    df = pd.DataFrame(rows)
    df = _inject_violations(df, answered_log, days)
    return df.sort_values("OUTBOUND_IVR_DT").reset_index(drop=True)


def _inject_violations(df: pd.DataFrame, answered_log: dict, days) -> pd.DataFrame:
    """Add the misfires the compliance engine is supposed to catch."""
    extra: list[dict] = []
    interaction = 900_000

    # 1. Cooldown breaches: customers re-surveyed 1-4 days after answering.
    responders = list(answered_log.items())
    RNG.shuffle(responders)
    for msisdn, when in responders[:220]:
        gap = float(RNG.uniform(0.5, 4.5))
        clock = when + dt.timedelta(days=gap)
        if clock.date() > days[-1]:
            continue
        interaction += 1
        answered = RNG.random() < 0.30
        extra.append({
            "MSISDN": msisdn,
            "OUTBOUND_IVR_DT": fire_time(clock.date()),
            "Q1_ANSWER: TNPS": (int(RNG.integers(0, 7)) if answered else None),
            "SURVEY_COMPLETION_FLAG": "Y" if answered else "N",
            "SHORT_CODE": str(RNG.choice(list(SHORT_CODES))),
            "AGENT_QUEUE": QUEUES[int(RNG.integers(0, len(QUEUES)))][0],
            "SR NUMBER": "".join(str(int(RNG.integers(0, 10))) for _ in range(12)),
            "INTERACTION_ID": f"I{interaction:09d}",
        })

    # 2. Over-cap trials: a 4th and 5th attempt on cycles that never answered.
    never = df[df["Q1_ANSWER: TNPS"].isna()].groupby("MSISDN").size()
    over = [m for m, n in never.items() if n >= 3][:160]
    for msisdn in over:
        last = df.loc[df["MSISDN"] == msisdn, "OUTBOUND_IVR_DT"].max()
        for step in (1, 2):
            clock = last + dt.timedelta(hours=float(RNG.uniform(5, 20)) * step)
            if clock.date() > days[-1]:
                break
            interaction += 1
            extra.append({
                "MSISDN": msisdn,
                "OUTBOUND_IVR_DT": fire_time(clock.date()),
                "Q1_ANSWER: TNPS": None,
                "SURVEY_COMPLETION_FLAG": "N",
                "SHORT_CODE": str(RNG.choice(list(SHORT_CODES))),
                "AGENT_QUEUE": QUEUES[int(RNG.integers(0, len(QUEUES)))][0],
                "SR NUMBER": "".join(str(int(RNG.integers(0, 10))) for _ in range(12)),
                "INTERACTION_ID": f"I{interaction:09d}",
            })

    df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)

    # 3. Out-of-window fires: a batch job that ran early, plus a late-night tail.
    idx = RNG.choice(df.index, size=int(len(df) * 0.03), replace=False)
    df.loc[idx, "OUTBOUND_IVR_DT"] = [
        fire_time(pd.Timestamp(ts).date(), in_window=False)
        for ts in df.loc[idx, "OUTBOUND_IVR_DT"]]

    # 4. Data problems worth reporting.
    bad = RNG.choice(df.index, size=6, replace=False)
    df.loc[bad[:3], "Q1_ANSWER: TNPS"] = 99          # out of range
    df.loc[bad[3], "OUTBOUND_IVR_DT"] = pd.NaT       # unreadable date
    df.loc[bad[4], "SHORT_CODE"] = "SC999"           # not in the mapping
    df.loc[bad[5], "SR NUMBER"] = "ABC-XYZ"          # non-numeric SR
    return df


def build_mappings() -> None:
    queues = pd.DataFrame([
        {"Agent_Queue": q, "Q Mapping Lev 1": "Care" if p in ("CARE", "PART") else "Commercial",
         "Q Mapping Lev 2 Combined": p, "Q Mapping Lev 3 New PF Seg": seg,
         "Q Mapping Lev 4 Seg": f"{seg} - {p}", "Site": "Cairo" if i % 2 else "Alexandria"}
        for i, (q, p, seg) in enumerate(QUEUES)])
    # A duplicate key and a queue that is missing from the mapping entirely.
    queues = pd.concat([queues, queues.head(1).assign(Site="Duplicate Row")],
                       ignore_index=True)

    codes = pd.DataFrame([
        {"SHORT_CODE": c, "Topic / Reason": t, "Category": cat, "Owner": own}
        for c, t, cat, own in [
            ("SC101", "Billing query", "Billing", "Finance Ops"),
            ("SC102", "Network issue", "Technical", "Network"),
            ("SC103", "Package change", "Commercial", "Marketing"),
            ("SC104", "Complaint handling", "Service", "CX"),
            ("SC105", "Activation delay", "Provisioning", "Field Ops"),
            ("SC106", "Repeat fault", "Technical", "Network"),
            ("SC107", "Information request", "Service", "CX"),
            ("SC108", "Escalation unresolved", "Service", "CX"),
        ]])

    with pd.ExcelWriter(HERE / "QueueMapping.xlsx", engine="xlsxwriter") as writer:
        pd.DataFrame({"How to use": ["Tab 2 holds the queue mapping.",
                                     "This tab is first on purpose."]}).to_excel(
            writer, sheet_name="Instructions", index=False)
        queues.to_excel(writer, sheet_name="Queue Mapping", index=False)
    codes.to_excel(HERE / "ShortCode_Mapping.xlsx", index=False, engine="xlsxwriter")


def main() -> None:
    df = build()
    # Header spelled differently on purpose - the loader must realign it.
    df = df.rename(columns={"OUTBOUND_IVR_DT": "outbound_ivr_dt"})
    out = HERE / "Survey_JanFeb2026.xlsx"
    df.to_excel(out, index=False, engine="xlsxwriter")
    build_mappings()
    answered = df["Q1_ANSWER: TNPS"].notna().sum()
    print(f"{out.name}: {len(df):,} fires, {df['MSISDN'].nunique():,} subscribers, "
          f"{answered:,} answered ({answered / len(df):.1%})")
    print("QueueMapping.xlsx (Instructions + Queue Mapping), ShortCode_Mapping.xlsx")


if __name__ == "__main__":
    main()
