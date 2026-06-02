"""Cinematic terminal effects for Roma - the "AI in a movie" feel.

Pure standard library. Everything degrades gracefully: if output isn't a real
terminal (piped to a file) or NO_COLOR is set, effects become plain instant
text so logs and scripts stay clean.

e& palette: deep red (#E00800) on black, with a few brighter red tones for the
boot/gradient animation.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

# --- e& red gradient (dark -> bright) as ANSI truecolor ---
_REDS = [(90, 0, 0), (150, 5, 3), (200, 6, 0), (224, 8, 0), (255, 60, 50)]
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
HIDE_CUR = "\x1b[?25l"
SHOW_CUR = "\x1b[?25h"


def _tty() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _enable_win_ansi() -> None:
    if os.name == "nt":
        os.system("")


def rgb(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


def red(level: int = 3) -> str:
    r, g, b = _REDS[max(0, min(level, len(_REDS) - 1))]
    return rgb(r, g, b)


def width() -> int:
    try:
        return shutil.get_terminal_size((80, 24)).columns
    except Exception:  # noqa: BLE001
        return 80


def type_out(text: str, color: str = "", delay: float = 0.012,
             newline: bool = True) -> None:
    """Print text character-by-character (typewriter effect)."""
    if not _tty():
        print(text, end="\n" if newline else "")
        return
    if color:
        sys.stdout.write(color)
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    if color:
        sys.stdout.write(RESET)
    if newline:
        sys.stdout.write("\n")
    sys.stdout.flush()


def boot_line(label: str, ok: str = "OK", delay: float = 0.35) -> None:
    """A boot/diagnostic line: 'label .... [ OK ]' with a brief pause."""
    if not _tty():
        print(f"  {label} [{ok}]")
        return
    dots = "." * max(3, 34 - len(label))
    sys.stdout.write(f"  {red(2)}{label} {DIM}{dots}{RESET} ")
    sys.stdout.flush()
    time.sleep(delay)
    sys.stdout.write(f"{red(4)}[ {ok} ]{RESET}\n")
    sys.stdout.flush()


def progress(label: str, steps: int = 22, delay: float = 0.03) -> None:
    """A short filling progress bar in e& red."""
    if not _tty():
        print(f"  {label} [done]")
        return
    sys.stdout.write(f"  {red(3)}{label}{RESET} ")
    for i in range(steps + 1):
        filled = "█" * i
        empty = "░" * (steps - i)
        sys.stdout.write(f"\r  {red(3)}{label}{RESET} {red(4)}{filled}{DIM}{empty}{RESET} "
                         f"{int(i / steps * 100):3d}%")
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


def gradient_block(lines: list[str]) -> str:
    """Colour each row of an ASCII block with a top-to-bottom red gradient."""
    if not _tty():
        return "\n".join(lines)
    n = len(lines)
    out = []
    for i, line in enumerate(lines):
        level = 1 + int((i / max(1, n - 1)) * 3)  # 1..4 down the block
        out.append(f"{red(level)}{line}{RESET}")
    return "\n".join(out)


def rule(char: str = "─", level: int = 2) -> str:
    w = min(width(), 64)
    line = char * w
    return f"{red(level)}{line}{RESET}" if _tty() else line


def hide_cursor() -> None:
    if _tty():
        sys.stdout.write(HIDE_CUR); sys.stdout.flush()


def show_cursor() -> None:
    if _tty():
        sys.stdout.write(SHOW_CUR); sys.stdout.flush()


def clear() -> None:
    if _tty():
        sys.stdout.write("\x1b[2J\x1b[H"); sys.stdout.flush()


# --- Animated "thinking" indicator (runs in a background thread) ----------- #

import threading


class Thinking:
    """A cinematic 'thinking' animation shown WHILE Roma computes an answer.

    Use as a context manager:

        with Thinking("analyzing your data"):
            result = do_work()

    On a real terminal it shows a pulsing braille spinner with cycling status
    words and a shifting red 'scanner' bar. On non-TTY it prints nothing, so
    piped output and logs stay clean.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    PHASES = ["analyzing", "scanning data", "computing", "cross-checking",
              "summarizing"]

    def __init__(self, label: str = "thinking"):
        self.label = label
        self._stop = threading.Event()
        self._thread = None
        self._on = _tty()

    def _run(self):
        i = 0
        bar_w = 14
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            phase = self.PHASES[(i // 6) % len(self.PHASES)]
            pos = i % (bar_w * 2)
            p = pos if pos < bar_w else (bar_w * 2 - pos)  # bounce
            bar = "".join("█" if abs(j - p) <= 1 else "·" for j in range(bar_w))
            sys.stdout.write(
                f"\r  {red(4)}{frame}{RESET} {red(3)}{phase}{RESET} "
                f"{red(2)}{bar}{RESET}   ")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1
        # erase the line on stop
        sys.stdout.write("\r" + " " * 56 + "\r")
        sys.stdout.flush()

    def __enter__(self):
        if self._on:
            hide_cursor()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._on:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=1)
            show_cursor()
        return False
