from __future__ import annotations

import re
from collections import Counter

from .text_utils import unique_preserving_order


_OPERATOR_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "polarity__negative",
        (
            r"\bmust\s+not\b",
            r"\bshall\s+not\b",
            r"\b(?:do|does|did)\s+not\b",
            r"\bcannot\b",
            r"\bcan't\b",
            r"\bnever\b",
            r"\bwithout\b",
            r"\bnot\b",
            r"\bno\b",
            r"\bnão\s+deve\b",
            r"\bnão\s+pode\b",
            r"\bnunca\b",
            r"\bjamais\b",
            r"\bsem\b",
            r"\bnão\b",
        ),
    ),
    (
        "modality__obligation",
        (
            r"\bmust\b",
            r"\bshall\b",
            r"\brequired\s+to\b",
            r"\bneeds?\s+to\b",
            r"\bhave\s+to\b",
            r"\bdeve(?:rá|m|rão)?\b",
            r"\bprecisa(?:m)?\b",
            r"\bé\s+obrigatório\b",
            r"\bobrigatório\b",
        ),
    ),
    (
        "modality__recommendation",
        (
            r"\bshould\b",
            r"\brecommended\b",
            r"\bdeveria(?:m)?\b",
            r"\brecomenda-se\b",
            r"\brecomendado\b",
        ),
    ),
    (
        "modality__permission_or_capability",
        (
            r"\bcan\b",
            r"\bmay\b",
            r"\bcould\b",
            r"\bpode(?:m|rá|rão)?\b",
            r"\bconsegue(?:m)?\b",
        ),
    ),
    (
        "condition__if",
        (r"\bif\b", r"\bse\b", r"\bcaso\b"),
    ),
    (
        "condition__when",
        (r"\bwhen\b", r"\bwhenever\b", r"\bquando\b", r"\bsempre\s+que\b"),
    ),
    (
        "condition__before",
        (r"\bbefore\b", r"\bantes\s+de\b", r"\bantes\b"),
    ),
    (
        "condition__after",
        (r"\bafter\b", r"\bdepois\s+de\b", r"\bapós\b"),
    ),
    (
        "scope__exception",
        (r"\bexcept\b", r"\bunless\b", r"\bexceto\b", r"\ba\s+menos\s+que\b"),
    ),
)

_COMPILED = tuple(
    (token, tuple(re.compile(pattern, re.IGNORECASE | re.UNICODE) for pattern in patterns))
    for token, patterns in _OPERATOR_PATTERNS
)


def extract_operator_tokens(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for token, patterns in _COMPILED:
        earliest: int | None = None
        for pattern in patterns:
            match = pattern.search(text)
            if match is not None and (earliest is None or match.start() < earliest):
                earliest = match.start()
        if earliest is not None:
            found.append((earliest, token))
    return unique_preserving_order(token for _position, token in sorted(found))


def operator_counter(text: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for token, patterns in _COMPILED:
        spans: list[tuple[int, int]] = []
        # Longest phrase wins within a category, avoiding double-counting "must not" and "not".
        matches: list[tuple[int, int]] = []
        for pattern in patterns:
            matches.extend(match.span() for match in pattern.finditer(text))
        for start, end in sorted(matches, key=lambda span: (-(span[1] - span[0]), span[0])):
            if any(start < old_end and end > old_start for old_start, old_end in spans):
                continue
            spans.append((start, end))
        counter[token] = len(spans)
    return counter
