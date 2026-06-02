"""Terminal look & feel for Roma: cinematic, e&-red-on-black welcome.

A movie-style boot sequence (gradient logo, diagnostic lines, progress bar,
typewriter greeting) the first time you open Roma. Everything degrades to plain
instant text when output isn't a real terminal, so logs/scripts stay clean.
"""

from __future__ import annotations

import os
import sys

from . import __version__, fx, knowledge

_RED = "\x1b[38;2;224;8;0m"
_GREY = "\x1b[38;2;150;150;150m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"

# Big block ROMA wordmark (rendered with a red gradient down the rows).
_LOGO = [
    "  ██████╗  ██████╗ ███╗   ███╗ █████╗ ",
    "  ██╔══██╗██╔═══██╗████╗ ████║██╔══██╗",
    "  ██████╔╝██║   ██║██╔████╔██║███████║",
    "  ██╔══██╗██║   ██║██║╚██╔╝██║██╔══██║",
    "  ██║  ██║╚██████╔╝██║ ╚═╝ ██║██║  ██║",
    "  ╚═╝  ╚═╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝",
]


def _color_on() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}" if _color_on() else text


def banner() -> str:
    return fx.gradient_block(_LOGO)


def _static_welcome(local_model: str | None) -> str:
    """Plain (non-animated) version - used when not a TTY, or as fallback."""
    name = knowledge.welcome_name()
    lines = [banner(), "",
             c(f"  Welcome, {name}.", _BOLD),
             c(f"  Roma v{__version__} - senior CX analyst, fully local.", _GREY)]
    extra = knowledge.extra_memory_files()
    mem = "work summary, CX playbook" + (f", +{len(extra)} of your files" if extra else "")
    lines.append(c(f"  Memory: {mem}.", _GREY))
    if local_model:
        lines.append(c(f"  Local model: {local_model} (natural language on).", _GREY))
    else:
        lines.append(c("  Mode: structured analyst.", _GREY))
    lines.append("")
    lines.append("  Try:  " + c("roma add", _BOLD) + "  ·  " +
                 c("roma watch", _BOLD) + "  ·  " +
                 c("roma compare April March", _BOLD) + "  ·  " +
                 c("roma export detractors", _BOLD))
    return "\n".join(lines)


def welcome(local_model: str | None, animate: bool = True) -> str:
    """Return the welcome text. If a TTY and animate, play the cinematic boot
    sequence (printed live) and return '' so the caller doesn't double-print."""
    if not (_color_on() and animate):
        return _static_welcome(local_model)

    name = knowledge.welcome_name()
    fx.hide_cursor()
    try:
        fx.clear()
        print()
        # Gradient logo, revealed row by row.
        import time
        for row in fx.gradient_block(_LOGO).split("\n"):
            print(row)
            time.sleep(0.05)
        print()
        print(fx.rule("─", 2))
        # Boot diagnostics.
        fx.boot_line("initializing core", "OK", 0.2)
        fx.boot_line("loading analyst memory", "OK", 0.25)
        mem_n = len(knowledge.extra_memory_files())
        fx.boot_line(f"indexing knowledge ({mem_n} extra files)"
                     if mem_n else "indexing knowledge", "OK", 0.2)
        if local_model:
            fx.boot_line(f"connecting local model: {local_model}", "OK", 0.25)
        else:
            fx.boot_line("scanning for local model", "none", 0.2)
        fx.progress("calibrating", steps=20, delay=0.02)
        print(fx.rule("─", 2))
        print()
        # Typewriter greeting.
        fx.type_out(f"  Welcome back, {name}.", fx.red(4) + _BOLD, 0.02)
        fx.type_out(f"  Roma v{__version__} online — your senior CX analyst, "
                    f"fully local.", fx.red(2), 0.006)
        mode = (f"natural-language mode ({local_model})" if local_model
                else "structured analyst mode")
        fx.type_out(f"  Status: {mode}.", _GREY, 0.004)
        print()
        print("  " + c("ready.", _BOLD) + c("  try:  ", _GREY)
              + c("roma watch", fx.red(4)) + c("  ·  ", _GREY)
              + c("roma compare April March", fx.red(4)) + c("  ·  ", _GREY)
              + c("roma export detractors", fx.red(4)))
    finally:
        fx.show_cursor()
    return ""  # already printed live
