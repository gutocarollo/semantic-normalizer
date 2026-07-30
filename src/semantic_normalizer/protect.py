from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProtectedSpan:
    start: int
    end: int
    kind: str
    value: str


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fenced_code", re.compile(r"```.*?```", re.DOTALL)),
    ("inline_code", re.compile(r"`[^`\n]+`")),
    ("url", re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
    ),
    ("windows_path", re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]*")),
    ("unix_path", re.compile(r"(?<!\w)/(?:[^/\s]+/)*[^/\s]*")),
    ("identifier", re.compile(r"\b[A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)+\b")),
    ("double_quote", re.compile(r'"(?:\\.|[^"\\])*"')),
    ("single_quote", re.compile(r"'(?:\\.|[^'\\])*'")),
)


def collect_protected_spans(text: str) -> list[ProtectedSpan]:
    candidates: list[ProtectedSpan] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            candidates.append(ProtectedSpan(match.start(), match.end(), kind, match.group(0)))

    # Prefer the longest span when patterns overlap, then restore source order.
    selected: list[ProtectedSpan] = []
    for candidate in sorted(
        candidates,
        key=lambda span: (-(span.end - span.start), span.start, span.end),
    ):
        if any(candidate.start < item.end and candidate.end > item.start for item in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda span: (span.start, span.end))
