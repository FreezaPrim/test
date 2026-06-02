"""Command-line interface for Roma."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__, config, database
from .ingest import ingest_file


# ----------------------------- output helpers ------------------------------ #

def _say(msg: str = "") -> None:
    print(msg)


def _summarise_ingest(res: dict) -> None:
    if "error" in res:
        _say(f"  x {res.get('file', '?')}: {res['error']}")
        return
    if res.get("kind") == "excel":
        _say(f"  + {res['file']} (Excel):")
        for t in res["tables"]:
            _say(f"      table '{t['name']}'  -  {t['rows']} rows, "
                 f"{len(t['columns'])} cols")
        return
    if res.get("kind") == "table":
        _say(f"  + {res['file']}  ->  table '{res['name']}'  "
             f"({res['rows']} rows, {len(res['columns'])} cols)")
        return
    if res.get("kind") == "document":
        pages = f", {res['pages']} pages" if "pages" in res else ""
        _say(f"  + {res['file']}  ->  document '{res['name']}' "
             f"({res['chunks']} chunks{pages})")
        return
    _say(f"  + {res}")


# ------------------------------- commands ---------------------------------- #

def _pick_folder() -> str | None:
    """Open a native folder-picker dialog. Returns None if unavailable/cancelled."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="Select a folder containing your data files")
        root.destroy()
        return folder or None
    except Exception:  # noqa: BLE001 - tkinter may be missing on some setups
        return None


def _collect_files(inputs: list[str], recursive: bool) -> list[str]:
    """Expand folders and globs into a de-duplicated list of file paths."""
    from .ingest import SUPPORTED_EXT
    found: list[str] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            found.extend(str(f) for f in sorted(it)
                         if f.is_file() and f.suffix.lower() in SUPPORTED_EXT)
        elif any(ch in item for ch in "*?["):
            found.extend(str(f) for f in sorted(Path().glob(item)) if f.is_file())
        else:
            found.append(item)
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def cmd_add(args) -> int:
    conn = database.connect()
    inputs = list(args.files)
    if not inputs:
        _say("Opening a folder picker... (choose the folder with your Excel files)")
        picked = _pick_folder()
        if not picked:
            _say("\nNothing selected. You can also pass a folder or files directly:")
            _say("   roma add \"C:\\path\\to\\folder\"")
            _say("   roma add data\\survey.xlsx data\\tickets.csv")
            return 1
        inputs = [picked]

    files = _collect_files(inputs, recursive=args.recursive)
    if not files:
        _say("No supported files found in what you gave me "
             "(looked for: xlsx, xls, csv, tsv, json, pdf, txt, md).")
        return 1

    _say(f"Ingesting {len(files)} file(s) into Roma...")
    ok = 0
    for f in files:
        res = ingest_file(conn, f)
        _summarise_ingest(res)
        if "error" not in res:
            ok += 1
    _say(f"\nDone. {ok}/{len(files)} file(s) loaded.")
    # Help Mohamed make sense of new data, whatever its format.
    try:
        from . import onboarding
        if database.list_tables(conn):
            _say("")
            _say(onboarding.suggest_text(conn))
    except Exception:  # noqa: BLE001
        pass
    # Proactive: surface notable changes the moment new data lands.
    try:
        from . import alerts
        if database.list_tables(conn):
            res = alerts.generate_alerts(conn)
            if "error" not in res and res["alerts"] and \
               res["alerts"][0] != "No notable month-over-month changes detected.":
                _say("")
                _say(alerts.alerts_text(conn))
    except Exception:  # noqa: BLE001
        pass
    return 0


def cmd_list(args) -> int:
    conn = database.connect()
    tables = database.list_tables(conn)
    docs = database.document_sources(conn)
    links = database.detect_links(conn)

    if not tables and not docs:
        _say("Roma has no data yet. Add some with:  roma add file1.xlsx file2.csv")
        return 0

    if tables:
        _say("TABLES")
        for t in tables:
            cols = ", ".join(c["name"] for c in t["columns"])
            _say(f"  {t['table']}  ({t['row_count']} rows)")
            _say(f"      columns: {cols}")
    if docs:
        _say("\nDOCUMENTS")
        for d in docs:
            _say(f"  {d['source']}  ({d['chunks']} chunks)")
    if links:
        _say("\nPOSSIBLE LINKS (shared columns Roma can join on)")
        for l in links:
            _say(f"  '{l['column']}' shared by: {', '.join(l['shared_by'])}")
    return 0


def _interactive_chat() -> int:
    from . import engine, localchat, ui
    conn = database.connect()

    ok, model = localchat.available()
    out = ui.welcome(model if ok else None, animate=True)
    if out:
        _say(out)
    _say("\n  Type a question, 'help' for examples, or 'exit' to quit.\n")

    while True:
        try:
            user = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            _say("\nbye.")
            return 0
        if not user:
            continue
        if user.lower() in {"exit", "quit", ":q"}:
            _say("bye.")
            return 0
        if user.lower() == "help":
            _say("\n" + engine.answer(conn, "what can you do", False, None) + "\n")
            continue
        from . import fx
        animate = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        try:
            if animate:
                with fx.Thinking("analyzing"):
                    ans = engine.answer(conn, user, use_llm=ok, model=model)
            else:
                ans = engine.answer(conn, user, use_llm=ok, model=model)
        except Exception as exc:  # noqa: BLE001
            _say(f"\n[error] {type(exc).__name__}: {exc}\n")
            continue
        if animate:
            sys.stdout.write(f"\n{fx.red(4)}{fx.BOLD}roma>{fx.RESET} ")
            sys.stdout.flush()
            # type out the first line for effect, print the rest instantly
            first, _, rest = ans.partition("\n")
            fx.type_out(first, fx.red(3), delay=0.006, newline=True)
            if rest:
                print(rest)
            print()
        else:
            _say(f"\nroma> {ans}\n")


def cmd_chat(args) -> int:
    return _interactive_chat()


def cmd_ask(args) -> int:
    from . import engine, localchat
    conn = database.connect()
    ok, model = localchat.available()
    _say(engine.answer(conn, args.question, use_llm=ok, model=model))
    return 0


def cmd_learn(args) -> int:
    from . import analyst
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No tabular data loaded yet. Add files first:  roma add <files>")
        return 1
    _say("Roma is learning from your data...")
    snap = analyst.learn(conn)
    d = snap["drivers"]
    if "error" in d:
        _say(f"  drivers: {d['error']}")
    else:
        top = ", ".join(f["feature"] for f in d["features"][:3])
        _say(f"  learned what drives '{d['target']}' - top factors: {top}")
    s = snap["segments"]
    if "error" not in s:
        _say(f"  found {s['k']} customer segments")
    _say("\nDone. Ask about it with 'roma chat', or get the full picture with 'roma report'.")
    return 0


def cmd_report(args) -> int:
    from .report import generate_report
    conn = database.connect()
    if not database.list_tables(conn) and not database.document_sources(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    _say("Roma is learning from your data and writing a report...\n")
    out = generate_report(conn, verbose=True)
    _say(f"\nReport written to: {out}")
    return 0


def cmd_onboard(args) -> int:
    from . import onboarding
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    onboarding.interactive_onboard(conn)
    return 0


def cmd_kpis(args) -> int:
    from . import kpis
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    res = kpis.compute(conn)
    if not res:
        _say("I couldn't detect standard CX KPI columns (NPS/CSAT/reach...). "
             "Run 'roma onboard' to tell me which columns are which.")
        return 0
    _say("CX KPIs from your data:")
    for k in res:
        _say(f"  - {k['name']}: {k['value']}   ({k['detail']})   [{k['table']}]")
    return 0


def _show_columns(title: str, columns: list[str]) -> None:
    _say(f"\n{title}")
    for i, col in enumerate(columns, 1):
        _say(f"   [{i}] {col}")


def cmd_map(args) -> int:
    from . import mapping
    conn = database.connect()

    # 1) base file
    base_file = args.base
    if not base_file:
        _say("Pick the BASE file (the one you want to add columns ONTO)...")
        base_file = _pick_file()
        if not base_file:
            _say("No file selected. Usage: roma map <base_file> <lookup_file>")
            return 1
    try:
        base_cols = mapping.columns_of(base_file)
    except Exception as exc:  # noqa: BLE001
        _say(f"Couldn't read base file: {exc}")
        return 1
    _show_columns(f"BASE file: {Path(base_file).name} - its columns:", base_cols)
    bk = input("\nWhich column is the KEY to match on? (number or name) > ").strip()
    base_key = mapping.resolve_choice(bk, base_cols)
    if not base_key:
        _say("That column wasn't recognised.")
        return 1

    # 2) lookup file
    lookup_file = args.lookup
    if not lookup_file:
        _say("\nPick the LOOKUP file (the one you'll pull columns FROM)...")
        lookup_file = _pick_file()
        if not lookup_file:
            _say("No lookup file selected.")
            return 1
    try:
        look_cols = mapping.columns_of(lookup_file)
    except Exception as exc:  # noqa: BLE001
        _say(f"Couldn't read lookup file: {exc}")
        return 1
    _show_columns(f"LOOKUP file: {Path(lookup_file).name} - its columns:", look_cols)
    lk = input(f"\nWhich column here matches '{base_key}'? (number or name) > ").strip()
    lookup_key = mapping.resolve_choice(lk, look_cols)
    if not lookup_key:
        _say("That column wasn't recognised.")
        return 1

    # 3) which columns to add
    _show_columns("Which columns do you want to ADD onto the base file?", look_cols)
    ac = input("\nList them (numbers or names, comma-separated; 'all' = everything) > ").strip()
    if ac.lower() == "all":
        add_cols = [c for c in look_cols if c != lookup_key]
    else:
        add_cols = mapping.resolve_multi(ac, look_cols)
    add_cols = [c for c in add_cols if c != lookup_key]
    if not add_cols:
        _say("No columns chosen to add.")
        return 1

    # 4) merge + load + export
    _say(f"\nMatching '{base_key}' <-> '{lookup_key}', adding: {', '.join(add_cols)} ...")
    res = mapping.do_merge(base_file, base_key, lookup_file, lookup_key, add_cols)
    if "error" in res:
        _say(f"Mapping failed: {res['error']}")
        return 1
    saved = mapping.save_and_load(res["df"], conn, name="mapped")
    _say(f"Done. Matched {res['matched_rows']} of {res['base_rows']} base rows.")
    _say(f"  Loaded as table '{saved['table']}' (ask Roma about it in chat).")
    _say(f"  Excel saved: {saved['file']}")
    return 0


def _pick_file() -> str | None:
    """Native file picker for a single spreadsheet/CSV; None if unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="Select a file",
            filetypes=[("Data files", "*.xlsx *.xls *.xlsm *.csv *.tsv"),
                       ("All files", "*.*")])
        root.destroy()
        return path or None
    except Exception:  # noqa: BLE001
        return None


def cmd_watch(args) -> int:
    from . import alerts
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    _say(alerts.alerts_text(conn))
    return 0


def cmd_compare(args) -> int:
    from . import detractors
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    cmp = detractors.compare(conn, args.period_a, args.period_b)
    if "error" in cmp:
        _say(f"Compare: {cmp['error']}")
        return 1
    a, b = cmp["a"], cmp["b"]
    _say(f"Detractors {a['period']} vs {b['period']}:")
    _say(f"  {a['period']}: {a['detractors']} ({a['pct']}%)")
    _say(f"  {b['period']}: {b['detractors']} ({b['pct']}%)")
    _say(f"  Change: {abs(cmp['delta'])} {cmp['direction']} "
         f"({cmp['rel_pct']:+}% relative, {cmp['pct_point_delta']:+} pts)")
    return 0


def cmd_teach(args) -> int:
    from . import learning
    if args.word and args.means:
        learning.teach_word(args.word, " ".join(args.means))
        _say(f"Learned: '{args.word}' = '{' '.join(args.means)}'.")
        return 0
    t = learning.all_teachings()
    pi, wm = t.get("phrase_intent", {}), t.get("word_meaning", {})
    if not pi and not wm:
        _say("I haven't been taught anything yet. In chat, correct me with "
             "'I meant drivers', or teach a word with 'churn means detractor'.")
        return 0
    if wm:
        _say("Learned words:")
        for w, m in wm.items():
            _say(f"   {w} -> {m}")
    if pi:
        _say("Learned phrasings:")
        for p, i in pi.items():
            _say(f"   \"{p}\" -> {i}")
    return 0


def cmd_forget(args) -> int:
    from . import learning
    if not args.yes:
        c = input("Forget everything Roma has learned from you? Type 'yes': ")
        if c.strip().lower() != "yes":
            _say("Cancelled.")
            return 1
    learning.forget_all()
    _say("Cleared everything I'd learned from your corrections.")
    return 0


def cmd_stats(args) -> int:
    from . import stats
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    _say(stats.summary_text(conn))
    c = stats.correlations(conn)
    if "error" not in c and c["pairs"]:
        _say("\nTop correlations:")
        for p in c["pairs"][:6]:
            _say(f"  - {p['a']} & {p['b']}: r={p['r']} ({p['strength']} {p['direction']})")
    return 0


def cmd_assumptions(args) -> int:
    from . import knowledge
    txt = knowledge._read("assumptions.md")
    _say(txt or "No assumptions file found.")
    return 0


def cmd_export(args) -> int:
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    fmt = (args.format or "excel").lower()
    what = (args.what or "report").lower()
    when = " ".join(args.when) if args.when else ""
    try:
        if fmt in ("excel", "xlsx", "xls"):
            from . import export_excel as ex
            if what in ("kpis", "kpi"):
                out = ex.export_kpis(conn)
            elif what in ("drivers", "driver"):
                out = ex.export_drivers(conn)
            elif what in ("detractors", "detractor"):
                out = ex.export_detractor_report(conn, when)
            else:
                out = ex.export_full(conn, when)
        elif fmt in ("pptx", "powerpoint", "ppt", "deck", "slides"):
            from . import export_docs as ed
            out = ed.export_pptx(conn, when)
        elif fmt in ("docx", "word", "doc"):
            from . import export_docs as ed
            out = ed.export_docx(conn, when)
        elif fmt == "pdf":
            from . import export_docs as ed
            out = ed.export_pdf(conn, when)
        else:
            _say(f"Format '{fmt}' isn't available. Use: excel, pptx, docx, pdf.")
            return 1
    except ImportError as exc:
        _say(f"That format needs a library that isn't installed ({exc.name}). "
             f"Re-run setup_windows.bat.")
        return 1
    except Exception as exc:  # noqa: BLE001
        _say(f"Export failed: {type(exc).__name__}: {exc}")
        return 1
    _say(f"Saved: {out}")
    return 0


def cmd_detractors(args) -> int:
    from . import detractors
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    time_q = " ".join(args.when) if args.when else ""
    rep = detractors.full_report(conn, time_q)
    if "error" in rep:
        _say(f"Detractors: {rep['error']}")
        return 1
    c = rep["count"]
    _say(f"Detractor report ({c.get('period','all data')}): "
         f"{c['detractors']} of {c['base']} respondents ({c['pct']}%), "
         f"scored 0-6 on {c['nps']}.")
    for b in rep["breakdowns"]:
        _say("")
        _say(f"By {b['dimension']}:")
        for r in b["rows"]:
            vals = list(r.values())
            _say(f"   {vals[0]}: {vals[1]}")
    rp = rep["repeated"]
    if "error" not in rp and rp["repeat_count"]:
        _say(f"\nRepeated detractors: {rp['repeat_count']} customers appeared "
             f"more than once (by {rp['id']}).")
    return 0


def cmd_welcome(args) -> int:
    from . import localchat, ui
    ok, model = localchat.available()
    out = ui.welcome(model if ok else None, animate=True)
    if out:
        _say(out)
    return 0


def cmd_reset(args) -> int:
    conn = database.connect()
    if not args.yes:
        confirm = input("This deletes ALL data Roma has loaded. Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            _say("Cancelled.")
            return 1
    database.reset(conn)
    _say("All loaded data cleared.")
    return 0


def cmd_tnps(args) -> int:
    """Generate the full TNPS analytics dashboard (Excel)."""
    from . import export_excel as ex
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    when = " ".join(args.when) if args.when else ""
    _say("Building TNPS dashboard (this may take a moment)...")
    try:
        out = ex.export_tnps_dashboard(conn, when)
        _say(f"Dashboard saved: {out}")
    except Exception as exc:  # noqa: BLE001
        _say(f"Error: {exc}")
        return 1
    return 0


def cmd_forecast(args) -> int:
    """Run Holt-Winters forecast on detractor trends."""
    from . import forecast as fc, tnps_analytics as ta
    conn = database.connect()
    if not database.list_tables(conn):
        _say("No data loaded yet. Add files first:  roma add <files>")
        return 1
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        _say("No TNPS data found.")
        return 1
    daily = ta.build_daily_trend(df, cols)
    result = fc.build_forecast(daily, horizon=args.horizon)
    if result.empty:
        _say("Not enough data to forecast.")
        return 1
    _say(f"Forecast for next {args.horizon} days:")
    for _, row in result.head(7).iterrows():
        _say(f"  {row['Date']}: ~{row.get('Forecast_Detractor_Rate_%', 0):.1f}% detractor rate")
    breach = result.attrs.get("breach_date")
    if breach:
        _say(f"  Warning: Projected breach of target detractor rate: {breach}")
    return 0


# --------------------------------- parser ---------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="roma",
        description="Roma - terminal AI analyst for customer-experience data.",
    )
    p.add_argument("--version", action="version", version=f"Roma {__version__}")
    sub = p.add_subparsers(dest="command")

    a = sub.add_parser("add", help="Load files or a whole folder into Roma.")
    a.add_argument("files", nargs="*",
                   help="Files, globs, or a FOLDER. Leave empty to open a folder picker.")
    a.add_argument("-r", "--recursive", action="store_true",
                   help="When given a folder, also include its sub-folders.")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="Show everything Roma has loaded.")
    l.set_defaults(func=cmd_list)

    c = sub.add_parser("chat", help="Start an interactive chat with Roma.")
    c.set_defaults(func=cmd_chat)

    k = sub.add_parser("ask", help="Ask one question and print the answer.")
    k.add_argument("question", help="Your question in quotes.")
    k.set_defaults(func=cmd_ask)

    le = sub.add_parser("learn", help="Learn patterns from the loaded data.")
    le.set_defaults(func=cmd_learn)

    ob = sub.add_parser("onboard", help="Tell Roma what your columns are (interactive).")
    ob.set_defaults(func=cmd_onboard)

    kp = sub.add_parser("kpis", help="Compute CX KPIs (NPS, CSAT, reach, ...).")
    kp.set_defaults(func=cmd_kpis)

    dt = sub.add_parser("detractors", help="Full detractor report (count, breakdowns, repeats).")
    dt.add_argument("when", nargs="*", help="Optional time window, e.g. April, Q1, 'last month'.")
    dt.set_defaults(func=cmd_detractors)

    ex = sub.add_parser("export", help="Export a report to a file (excel/pptx/docx/pdf).")
    ex.add_argument("what", nargs="?", default="report",
                    help="report | detractors | kpis | drivers")
    ex.add_argument("--format", "-f", default="excel",
                    help="excel | pptx | docx | pdf")
    ex.add_argument("--when", nargs="*", help="Optional time window, e.g. April, Q1.")
    ex.set_defaults(func=cmd_export)

    st = sub.add_parser("stats", help="Descriptive statistics & correlations.")
    st.set_defaults(func=cmd_stats)

    wa = sub.add_parser("watch", help="Auto-detect notable month-over-month changes.")
    wa.set_defaults(func=cmd_watch)

    mp = sub.add_parser("map", help="Map/merge two files on a key column (VLOOKUP-style).")
    mp.add_argument("base", nargs="?", help="Base file (add columns onto this).")
    mp.add_argument("lookup", nargs="?", help="Lookup file (pull columns from this).")
    mp.set_defaults(func=cmd_map)

    cm = sub.add_parser("compare", help="Compare detractors between two periods.")
    cm.add_argument("period_a", help="First period, e.g. April")
    cm.add_argument("period_b", help="Second period, e.g. March")
    cm.set_defaults(func=cmd_compare)

    asm = sub.add_parser("assumptions", help="Show the rules Roma analyses by.")
    asm.set_defaults(func=cmd_assumptions)

    tc = sub.add_parser("teach", help="Teach Roma a word, or view what it has learned.")
    tc.add_argument("word", nargs="?", help="The word to teach (e.g. churn).")
    tc.add_argument("means", nargs="*", help="What it means (e.g. detractor).")
    tc.set_defaults(func=cmd_teach)

    fg = sub.add_parser("forget", help="Clear everything Roma learned from you.")
    fg.add_argument("--yes", action="store_true", help="Skip confirmation.")
    fg.set_defaults(func=cmd_forget)

    w = sub.add_parser("welcome", help="Show the Roma welcome screen.")
    w.set_defaults(func=cmd_welcome)

    r = sub.add_parser("report", help="Generate an auto insights report.")
    r.set_defaults(func=cmd_report)

    z = sub.add_parser("reset", help="Delete all loaded data.")
    z.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    z.set_defaults(func=cmd_reset)

    tn = sub.add_parser("tnps", help="Generate full TNPS analytics dashboard (Excel).")
    tn.add_argument("when", nargs="*", help="Optional time window, e.g. April, Q1.")
    tn.set_defaults(func=cmd_tnps)

    fr = sub.add_parser("forecast", help="Forecast detractor trends (Holt-Winters).")
    fr.add_argument("--horizon", type=int, default=30, help="Days to forecast (default 30).")
    fr.set_defaults(func=cmd_forecast)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        from . import localchat, ui
        ok, model = localchat.available()
        out = ui.welcome(model if ok else None, animate=True)
        if out:
            _say(out)
        _say("\n  Run 'roma -h' to see all commands.")
        return 0
    try:
        return args.func(args)
    except RuntimeError as exc:  # e.g. missing API key - already friendly
        _say(f"\n{exc}")
        return 2
    except KeyboardInterrupt:
        _say("\nCancelled.")
        return 130
    except Exception as exc:  # noqa: BLE001 - never dump a raw traceback at the user
        name = type(exc).__name__
        msg = str(exc)
        _say("\n[Roma ran into a problem]")
        _say(f"{name}: {msg}")
        _say("If this keeps happening, run 'roma list' to check your data loaded "
             "correctly, or re-run setup_windows.bat.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
