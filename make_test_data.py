"""Build a deliberately messy synthetic Micro export + mapping workbook.

Every awkward shape the loader claims to handle is present on purpose:
misspelled headers, an instructions tab before the real mapping, duplicate and
blank mapping keys, an agent missing from the mapping, a blank queue, queue
names that the old substring rules misclassified, repeated call identifiers,
duplicate interaction ids, reversed timestamps, unparsable durations, and two
agents who share a display name.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

RNG = np.random.default_rng(20260216)
HERE = Path(__file__).resolve().parent


def hms(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


AGENTS = [
    # login,  name,             team,        business type,  spv,          queue
    ("E1001", "Mona Salah",     "Team Alpha", "Consumer",    "Nadia Farouk", "Consumer Care"),
    ("E1002", "Karim Adel",     "Team Alpha", "Consumer",    "Nadia Farouk", "Consumer Care"),
    ("E1003", "Hala Mostafa",   "Team Alpha", "Consumer",    "Nadia Farouk", "Consumer Care"),
    ("E1004", "Omar Zaki",      "Team Beta",  "Enterprise",  "Sherif Amin",  "Enterprise Sales"),
    ("E1005", "Dina Fouad",     "Team Beta",  "Enterprise",  "Sherif Amin",  "Enterprise Sales"),
    ("E1006", "Mona Salah",     "Team Beta",  "Enterprise",  "Sherif Amin",  "Enterprise Sales"),
    ("E1007", "Tarek Nabil",    "Team Gamma", "Internet",    "Rania Wahba",  "Internet ADSL"),
    ("E1008", "Yara Hassan",    "Team Gamma", "Internet",    "Rania Wahba",  "Internet ADSL"),
    ("E1009", "Amr Sobhy",      "Team Delta", "Non Telecom", "Rania Wahba",  "NON_TELECOM Desk"),
    ("E1010", "Nour Ibrahim",   "Team Delta", "Non Telecom", "Rania Wahba",  "NON_TELECOM Desk"),
    ("E1011", "Sara Kamal",     "Team Alpha", "Consumer",    "Nadia Farouk", "Retention"),
    # Deliberately absent from the mapping: business type must fall back to the
    # queue name, which under the old "ent" rule wrongly read as Enterprise.
    ("E1012", None,             None,         None,          None,           "Retention"),
]

DAYS = [dt.date(2026, 2, d) for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)]


def build_micro() -> pd.DataFrame:
    rows: list[dict] = []
    interaction = 500_000
    # A per-agent pace, so the centre has genuinely busy and genuinely quiet
    # agents and the Watchlist picks out a few rather than flagging everyone.
    pace = {login: float(RNG.uniform(0.75, 1.65)) for login, *_ in AGENTS}
    for login, _name, _team, btype, _spv, queue in AGENTS:
        for day in DAYS:
            if RNG.random() < 0.08:          # the odd day off
                continue
            clock = dt.datetime.combine(day, dt.time(9, 0))
            for _ in range(200):
                interaction += 1
                clock += dt.timedelta(
                    minutes=float(RNG.uniform(4.0, 12.0)) * pace[login]
                )
                if clock.hour >= 17:
                    break

                is_call = 1 if RNG.random() > 0.12 else 0
                base = {"Consumer": 95, "Enterprise": 150, "Internet": 120}.get(btype or "", 80)
                handle = float(max(4, RNG.normal(base, base * 0.5))) if is_call else float(RNG.integers(3, 40))
                hold = float(max(0, RNG.normal(18, 22))) if is_call else 0.0
                dial = float(RNG.integers(5, 40))
                wrap = float(max(0, RNG.normal(95, 60))) if is_call and RNG.random() > 0.25 else 0.0
                total = handle + hold + wrap
                end = clock + dt.timedelta(seconds=total)

                # One call can touch the agent twice: Identifier repeats so that
                # unique long calls come out below the reached-call count.
                identifier = f"C{interaction // 2:07d}"

                rows.append({
                    "Month": "FEB2026",
                    "Day": day.isoformat(),
                    "Interval": f"{clock.hour}:00",          # "9:00", not "09:00"
                    "Queue": queue,
                    "Identifier": identifier,
                    "Call 1 - Trial 0": is_call,
                    "login_id": login,                        # header needs aligning
                    "SPV": "EXPORT_SPV_STALE",                # mapping must win
                    "INTERACTION_ID": f"I{interaction:08d}",
                    "START_TS_TIME": clock,
                    "END_TS_TIME": end,
                    "Segment": RNG.choice(["Billing", "Technical", "Sales", "Complaint"]),
                    "Handle Time": hms(handle),
                    "Total Duration (Fmt)": hms(total),
                    "Customer Handle Time (Fmt)": hms(handle),
                    "Customer Dial Time (Fmt)": round(dial, 1),   # plain seconds, not hh:mm:ss
                    "Customer Hold Time (Fmt)": hms(hold),
                    "Customer Wrap Time (Fmt)": hms(wrap),
                    "QUEUE_TIME": int(RNG.integers(0, 120)),
                })

    frame = pd.DataFrame(rows)

    # --- deliberate data problems, so the Data Quality sheet has something to say
    frame.loc[3, "Customer Handle Time (Fmt)"] = "not a time"     # unparsable
    frame.loc[7, "START_TS_TIME"] = pd.NaT                        # missing timestamp
    frame.loc[11, "END_TS_TIME"] = frame.loc[11, "START_TS_TIME"] - dt.timedelta(minutes=5)
    frame.loc[15, "INTERACTION_ID"] = frame.loc[14, "INTERACTION_ID"]  # duplicate id
    frame.loc[19, "Queue"] = ""                                   # blank -> (blank) slice
    frame.loc[20, "Queue"] = np.nan
    frame.loc[23, "Call 1 - Trial 0"] = 2                         # out of range flag
    return frame


def build_mapping(path: Path) -> None:
    instructions = pd.DataFrame({
        "How to use this file": [
            "Tab 'Agent Mapping' holds one row per agent.",
            "Tab 'Queue Mapping' groups queues for reporting.",
            "This tab is first on purpose: the loader must skip it.",
        ]
    })

    agents = pd.DataFrame(
        [
            {"Login_ID": login, "Agent": name, "Team": team,
             "Business Type": btype, "SPV": spv}
            for login, name, team, btype, spv, _q in AGENTS
            if name is not None
        ]
    )
    # A duplicate key (first wins) and a blank row, both of which must be dropped.
    agents = pd.concat([
        agents,
        pd.DataFrame([{"Login_ID": "E1001", "Agent": "Mona Salah (old record)",
                       "Team": "Team Zeta", "Business Type": "Enterprise",
                       "SPV": "Wrong Person"}]),
        pd.DataFrame([{"Login_ID": None, "Agent": None, "Team": None,
                       "Business Type": None, "SPV": None}]),
    ], ignore_index=True)

    queues = pd.DataFrame([
        {"Queue": "Consumer Care",    "Queue Group": "Care",      "Channel": "Voice"},
        {"Queue": "Enterprise Sales", "Queue Group": "Sales",     "Channel": "Voice"},
        {"Queue": "Internet ADSL",    "Queue Group": "Technical", "Channel": "Voice"},
        {"Queue": "NON_TELECOM Desk", "Queue Group": "Partners",  "Channel": "Voice"},
        # "Retention" is intentionally absent -> lookup gap -> "Unmapped"
    ])

    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        instructions.to_excel(writer, sheet_name="Instructions", index=False)
        agents.to_excel(writer, sheet_name="Agent Mapping", index=False)
        queues.to_excel(writer, sheet_name="Queue Mapping", index=False)


def main() -> None:
    micro = build_micro()
    micro_path = HERE / "FEB2026_Micro.xlsx"
    micro.to_excel(micro_path, index=False, engine="xlsxwriter")
    build_mapping(HERE / "Agent Mapping.xlsx")
    print(f"{micro_path.name}: {len(micro):,} rows, {micro['login_id'].nunique()} agents, "
          f"{micro['Queue'].nunique()} queues")
    print("Agent Mapping.xlsx: Instructions / Agent Mapping / Queue Mapping")


if __name__ == "__main__":
    main()
