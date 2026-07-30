from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_WORD_RE = re.compile(r"(?u)\b[\w]+\b")
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)|\n+", re.MULTILINE)

_EN_HINTS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "must",
    "should",
    "can",
    "before",
    "after",
    "when",
    "with",
    "without",
    "from",
    "to",
    "of",
    "is",
    "are",
}
_PT_HINTS = {
    "o",
    "a",
    "os",
    "as",
    "um",
    "uma",
    "e",
    "ou",
    "deve",
    "pode",
    "antes",
    "depois",
    "quando",
    "com",
    "sem",
    "para",
    "do",
    "da",
    "é",
    "são",
    "não",
}


def normalize_key(value: str) -> str:
    """Normalize a label for exact registry lookup without deleting accents."""
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.split())


def tokenise(value: str) -> list[str]:
    value = unicodedata.normalize("NFKC", value).casefold()
    return _WORD_RE.findall(value)


def detect_language(value: str) -> str:
    tokens = tokenise(value)
    if not tokens:
        return "und"
    en = sum(token in _EN_HINTS for token in tokens)
    pt = sum(token in _PT_HINTS for token in tokens)
    accented = sum(any(ch in "áàâãéêíóôõúüç" for ch in token) for token in tokens)
    pt += accented
    if en == pt == 0:
        return "und"
    if pt > en:
        return "pt"
    if en > pt:
        return "en"
    return "und"


def concept_token(concept_id: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "__", concept_id.casefold()).strip("_")
    return f"c__{cleaned}"


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _SENTENCE_RE.finditer(text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
    if not spans and text:
        spans.append((0, len(text)))
    return spans


def overlaps(start: int, end: int, intervals: Iterable[tuple[int, int]]) -> bool:
    return any(start < interval_end and end > interval_start for interval_start, interval_end in intervals)
