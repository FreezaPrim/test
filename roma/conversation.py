"""Per-session conversation context for Roma chat.

Architecture based on OpenJarvis's Conversation + Message dataclasses
(src/openjarvis/core/types.py), adapted for Roma's sync, offline-first,
stdlib-only design.

Key addition over OpenJarvis: `resolve_followup()` which injects the
last query's dimension/table/time into vague follow-up questions so
builders can answer them without conversational state in the SQL layer.
"""

from __future__ import annotations

import re
from typing import Any


class Message:
    """A single chat message (role + content)."""

    __slots__ = ("role", "content", "metadata")

    def __init__(self, role: str, content: str,
                 metadata: dict[str, Any] | None = None) -> None:
        self.role = role
        self.content = content
        self.metadata: dict[str, Any] = metadata or {}

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    def __repr__(self) -> str:
        preview = self.content[:60].replace("\n", " ")
        return f"Message(role={self.role!r}, content={preview!r})"


class Conversation:
    """Ordered message history with follow-up context resolution.

    Holds two parallel things:
    1. Raw message list → fed to the LLM when use_llm=True
    2. Structured context attrs (last_dim / last_table / last_time)
       → used by builders to expand vague follow-up queries

    Sliding window: oldest messages are dropped when max_messages reached.
    """

    def __init__(self, system_prompt: str = "", max_messages: int = 20) -> None:
        self._system = system_prompt
        self._messages: list[Message] = []
        self._max = max_messages

        # structured context from the last successful builder result
        self.last_dim: str = ""
        self.last_table: str = ""
        self.last_time: str = ""
        self.last_answer: str = ""

    # ── message management ────────────────────────────────────────────────

    def add_user(self, text: str) -> None:
        self._messages.append(Message("user", text))
        self._trim()

    def add_assistant(self, text: str, *, update_last: bool = True) -> None:
        self._messages.append(Message("assistant", text))
        if update_last:
            self.last_answer = text
        self._trim()

    def _trim(self) -> None:
        if len(self._messages) > self._max:
            self._messages = self._messages[-self._max:]

    def clear(self) -> None:
        self._messages.clear()
        self.last_dim = self.last_table = self.last_time = self.last_answer = ""

    # ── LLM formatting ────────────────────────────────────────────────────

    def to_llm_messages(self, window: int = 10) -> list[dict[str, str]]:
        """Format for LLM API: [system] + last *window* messages."""
        msgs: list[dict[str, str]] = []
        if self._system:
            msgs.append({"role": "system", "content": self._system})
        msgs.extend(m.to_dict() for m in self._messages[-window:])
        return msgs

    def recent_text(self, n: int = 4) -> str:
        """Last *n* turns as readable text — injected into builder context."""
        lines = []
        for m in self._messages[-n:]:
            prefix = "User" if m.role == "user" else "Roma"
            lines.append(f"{prefix}: {m.content[:300]}")
        return "\n".join(lines)

    # ── follow-up expansion ───────────────────────────────────────────────

    def resolve_followup(self, question: str) -> str:
        """Expand vague follow-up references using stored context.

        Examples resolved:
          "now in April"          → "print top detractors by <last_dim> in April"
          "filter by Q2"          → "print top detractors by <last_dim> in Q2"
          "same for March"        → "print top detractors by <last_dim> in March"
          "show that in the pptx" → unchanged (export intent, not a filter)
        """
        q = question.lower().strip()

        # nothing to resolve if no prior context
        if not self.last_dim:
            return question

        # skip if it's an export / save intent
        if re.search(r"\bexport\b|\bsave\b|\bpptx\b|\bexcel\b|\bpdf\b", q):
            return question

        # follow-up time-filter: "now in April", "filter by Q1", "same for March"
        time_ref = re.search(
            r"\b(in|for|filter.*by|same.*for|now.*in)\s+"
            r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
            r"dec(?:ember)?|q[1-4]|last month|this month|this year|20\d{2})",
            q,
        )
        if time_ref and re.search(r"\b(that|same|those|it|this)\b", q):
            period = time_ref.group(2)
            return (f"print top detractors by {self.last_dim} in {period}"
                    + (" [last_table=" + self.last_table + "]" if self.last_table else ""))

        # "show the same" / "same breakdown" without a dimension in the question
        if re.search(r"\bsame\b|\brepeat\b|\bagain\b", q):
            if not re.search(
                r"shortcode|queue|team|channel|agent|call.?type|owner|topic", q
            ):
                return f"print top detractors by {self.last_dim}"

        return question

    # ── context update (called by engine after a builder fires) ──────────

    def update_context(self, dim: str = "", table: str = "", time: str = "") -> None:
        if dim:   self.last_dim   = dim
        if table: self.last_table = table
        if time:  self.last_time  = time
