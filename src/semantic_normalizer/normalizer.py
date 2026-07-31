#!/usr/bin/env python3
"""Deterministic EN/pt-BR controlled-language sidecar and retrieval evaluator."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import math
import random
import re
import sys
import tokenize
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from .registry import ContractError as RegistryContractError
from .registry import automatic_surfaces, load_registry
from .schema_validation import validate_sidecar_record

SCHEMA_VERSION = "2.0.0"
TOOL_VERSION = "2.0.0"
LANGUAGES = ("en", "pt-BR")
KNOWN_SUFFIXES = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".py": "python"}
WORD_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿ\u0300-\u036f]+(?:[-.][\wÀ-ÖØ-öø-ÿ\u0300-\u036f]+)*", re.UNICODE)
FUNCTION_WORDS = {
    "en": {"the", "a", "an", "and", "or", "to", "of", "in", "for", "with", "this", "that", "is", "are"},
    # Widened after a self-audit of the detector repair. With sixteen entries `Somatório dos PV`
    # scored ZERO for Portuguese — `dos` was absent — and a Portuguese line of arithmetic came out
    # English; `Assim, ele lucrará R$ 20 mil reais ao total.` scored nothing on either side and
    # came out `unknown`. The verdict is a RATIO between two counts, so a list missing the
    # commonest words of one language does not make that language lose narrowly, it makes it score
    # nothing at all.
    "pt-BR": {"o", "a", "os", "as", "e", "ou", "de", "do", "da", "dos", "das", "em", "no", "na",
              "nos", "nas", "para", "com", "este", "esta", "é", "são", "que", "um", "uma", "por",
              "ao", "aos", "à", "às", "se", "não", "pelo", "pela", "pelos", "pelas", "como", "mas",
              "seu", "sua", "seus", "suas", "ser", "foi", "tem", "há"},
}
COORDINATION = {"and", "or", "e", "ou"}
ANAPHORA = {"it", "they", "them", "this", "that", "ele", "ela", "eles", "elas", "isso", "isto"}


class ContractError(ValueError):
    """A fail-closed contract error."""


ContractError = RegistryContractError


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nfc_casefold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def ascii_fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).encode(
        "ascii", "ignore"
    ).decode("ascii").casefold()


def utf8_offset(text: str, cp: int) -> int:
    return len(text[:cp].encode("utf-8"))


def line_col(text: str, cp: int) -> tuple[int, int]:
    line = text.count("\n", 0, cp) + 1
    last = text.rfind("\n", 0, cp)
    return line, cp if last < 0 else cp - last - 1


def read_utf8(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{path}: input is not valid UTF-8: {exc}") from exc


load_lexicon = load_registry


def merge_spans(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    result: list[tuple[int, int, str]] = []
    for start, end, kind in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]), x[2])):
        if start >= end:
            continue
        if result and start < result[-1][1]:
            if end <= result[-1][1]:
                continue
            start = result[-1][1]
        result.append((start, end, kind))
    return result


def regex_spans(text: str, markdown: bool) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in re.finditer(
        r"<(?P<tag>code|pre)\b[^>]*>.*?</(?P=tag)\s*>",
        text,
        re.IGNORECASE | re.DOTALL,
    ):
        spans.append(
            (
                match.start(),
                match.end(),
                f"html_{match.group('tag').casefold()}_block",
            )
        )
    if markdown:
        if text.startswith("---\n"):
            match = re.search(r"(?m)^---\s*$", text[4:])
            if match:
                spans.append((0, 4 + match.end(), "frontmatter"))
        for match in re.finditer(r"(?ms)^(```|~~~).*?^\1[^\n]*(?:\n|$)", text):
            spans.append((match.start(), match.end(), "fenced_code"))
        patterns = [
            (r"`[^`\n]+`", "inline_code"),
            (r"!?\[[^\]\n]*\]\(([^)\n]+)\)", "markdown_link"),
            (r"https?://[^\s<>\])]+", "url"),
            (r"<!--.*?-->|</?[A-Za-z][^>]*>", "html"),
        ]
        for pattern, kind in patterns:
            for match in re.finditer(pattern, text, re.DOTALL):
                if kind == "markdown_link":
                    start, end = match.span(1)
                    spans.append((start, end, "link_destination"))
                else:
                    spans.append((match.start(), match.end(), kind))
    structured = [
        (r"\$\{\{.*?\}\}|\{\{.*?\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\}|%\([^)]+\)s|<%.*?%>", "placeholder"),
        (r"(?<!\w)(?:--?[A-Za-z][\w-]*|[A-Za-z]:[\\/][^\s]+|(?:\.{0,2}/|/)[^\s`]+)", "path_or_flag"),
        (r"\b(?:v?\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9.-]+)?)\b", "version"),
        (r"\b[0-9a-fA-F]{32,64}\b", "hash"),
        (r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b", "identifier"),
        (r"\b\d+(?:[.,]\d+)?\s?(?:%|ms|s|min|h|Hz|kHz|MHz|GB|MB|KB|B|kg|g|km|m|cm|mm|°C|°F)\b", "number_unit"),
        (r"(?<![\w.-])\d+(?:[.,]\d+)?(?![\w.-])", "number"),
        (r"\b(?:[A-Za-z]+_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*|[A-Za-z][\w-]*\.[A-Za-z0-9_.-]+)\b", "identifier"),
    ]
    for pattern, kind in structured:
        for match in re.finditer(pattern, text):
            spans.append((match.start(), match.end(), kind))
    return merge_spans(spans)


def infer_kind(path: Path | None, explicit: str) -> str:
    if explicit != "auto":
        return explicit
    if path is None:
        return "text"
    kind = KNOWN_SUFFIXES.get(path.suffix.casefold())
    if not kind:
        raise ContractError(f"{path}: unknown suffix; pass --kind text only for explicitly pre-extracted text")
    return kind


def python_segments(text: str) -> list[tuple[int, int, str]]:
    lines = text.splitlines(keepends=True)
    starts, total = [], 0
    for line in lines:
        starts.append(total)
        total += len(line)
    if not lines or (text and not text.endswith("\n")):
        starts.append(total)

    def token_cp(pos: tuple[int, int]) -> int:
        row, column = pos
        return starts[row - 1] + column

    def ast_cp(pos: tuple[int, int]) -> int:
        row, col_bytes = pos
        line = lines[row - 1] if row - 1 < len(lines) else ""
        return starts[row - 1] + len(line.encode("utf-8")[:col_bytes].decode("utf-8", "ignore"))

    try:
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, tokenize.TokenError, IndentationError) as exc:
        raise ContractError(f"invalid Python input: {exc}") from exc
    doc_positions = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if body and isinstance(body, list) and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                doc_positions.add((value.lineno, value.col_offset))
    result = []
    for token in tokens:
        if token.type == tokenize.COMMENT:
            start, end = token_cp(token.start), token_cp(token.end)
            content_start = start + 1
            if content_start < end and text[content_start] == " ":
                content_start += 1
            result.append((content_start, end, "python_comment"))
        elif token.type == tokenize.STRING:
            token_start = token_cp(token.start)
            # ast columns are UTF-8 bytes; tokenize columns are characters. Compare line and source prefix.
            is_doc = any(pos[0] == token.start[0] and ast_cp(pos) == token_start for pos in doc_positions)
            if not is_doc:
                continue
            match = re.match(r"(?is)(?:[rubf]*)('''|\"\"\"|'|\")", token.string)
            if not match:
                continue
            quote = match.group(1)
            start = token_start + match.end()
            end = token_cp(token.end) - len(quote)
            if start <= end:
                result.append((start, end, "python_docstring"))
    return sorted(result)


def line_segments(text: str) -> list[tuple[int, int, str]]:
    result = []
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line.rstrip("\r\n"))
        if text[offset:end].strip():
            result.append((offset, end, "line"))
        offset += len(line)
    if not text and not result:
        return []
    return result


def overlaps(start: int, end: int, spans: list[tuple[int, int, str]]) -> bool:
    return any(start < other_end and end > other_start for other_start, other_end, _ in spans)


def splits_protected(start: int, end: int, spans: list[tuple[int, int, str]]) -> bool:
    """True when a candidate CROSSES the boundary of a protected span rather than containing it.

    Protection exists so that a lexical form cannot mangle a number, a URL, a code block or an
    identifier by matching part of one. A candidate that fully CONTAINS a protected span mangles
    nothing — the number survives intact inside the alias — and refusing it is what kept the most
    important regulatory concept in this corpus unmatchable: `Resolução CVM 175` occurs 94 times
    and heads 65 sections, and every one of them was skipped because `175` is a protected number
    span. Same for `Resolução CMN 2.554` and `Resolução CMN 4.557`.

    So the test is crossing, not touching. The existing escape hatch in `normalize_text` — a
    protected span whose text IS an automatic surface is dropped from the list — already says the
    intent is to protect structure from being split, not to make structure unmentionable.
    """
    for other_start, other_end, _kind in spans:
        if start < other_end and end > other_start and not (start <= other_start and end >= other_end):
            return True
    return False


def token_key(text: str) -> tuple[str, ...]:
    return tuple(nfc_casefold(match.group()) for match in WORD_RE.finditer(text))


def lexical_matches(segment: str, segment_start: int, protected: list[tuple[int, int, str]], lexicon: dict):
    tokens = [(m.start(), m.end(), nfc_casefold(m.group())) for m in WORD_RE.finditer(segment)]
    auto_by_tokens: dict[tuple[str, ...], tuple] = {}
    review_by_tokens: dict[tuple[str, ...], list] = defaultdict(list)
    for key, value in lexicon["automatic"].items():
        parts = token_key(key)
        if parts:
            auto_by_tokens[parts] = value
    for key, values in lexicon["reviews"].items():
        parts = token_key(key)
        if parts:
            review_by_tokens[parts].extend(values)
    max_len = max([len(k) for k in auto_by_tokens] + [len(k) for k in review_by_tokens] + [1])

    # Collect EVERY candidate, then resolve overlaps globally. The scan used to be greedy from
    # the left: at each position it took the longest form starting THERE and jumped past it, so
    # a longer form beginning one token later never got tried. An adversarial review proved it
    # with two sentences over the same phrase —
    #
    #   "Growth at a Reasonable Price (GARP) é um estilo."   -> technical.garp        (correct)
    #   "O estilo Growth at a Reasonable Price combina..."   -> `estilo Growth` wins,
    #                                                           GARP lost, `Price` fires bare
    #
    # — one span producing a recall loss and a bare fragment at once. It matters far beyond two
    # sentences: "register the compound and longest-match retires the fragment" was the stated
    # justification for dozens of repairs across five review rounds, and under a leftmost scan
    # that guarantee holds only when the competing forms start at the same token. The review
    # enumerated 397 pairs where a proper suffix of one automatic form is the head of another —
    # `alocação de capital` × `capital regulatório`, `active risk` × `risk management`,
    # `achatamento da curva` × `curva de juros` — every one of them latent.
    candidates: list[tuple[int, int, int, str, tuple, object]] = []
    for index in range(len(tokens)):
        for size in range(min(max_len, len(tokens) - index), 0, -1):
            key = tuple(item[2] for item in tokens[index:index + size])
            start, end = tokens[index][0], tokens[index + size - 1][1]
            if splits_protected(segment_start + start, segment_start + end, protected):
                continue
            if key in review_by_tokens:
                names = sorted({item[0] for item in review_by_tokens[key]})
                # Any review ambiguity dominates an automatic mapping for safety.
                if len(names) > 1 or key not in auto_by_tokens or auto_by_tokens[key][0] not in names:
                    candidates.append((size, index, start, "review", key, review_by_tokens[key]))
                    continue
            if key in auto_by_tokens:
                candidates.append((size, index, start, "auto", key, auto_by_tokens[key]))

    # Longest first; ties broken leftmost, so the result stays deterministic and independent of
    # the order the registry happened to be written in.
    candidates.sort(key=lambda item: (-item[0], item[1]))

    events, ambiguous = [], []
    taken: list[tuple[int, int]] = []
    for size, index, start, kind, _key, value in candidates:
        end = tokens[index + size - 1][1]
        if any(start < other_end and end > other_start for other_start, other_end in taken):
            continue
        alias = segment[start:end]
        if kind == "review":
            resolved = []
            for cid, lang, term, source in sorted(value):
                record = lexicon["by_id"][cid]
                resolved.append({
                    "concept_id": cid, "language": lang, "term": term, "sense": record["sense"],
                    "semantic_class": record["semantic_class"], "pos": record["pos"], "source": source,
                })
            ambiguous.append({
                "start": segment_start + start, "end": segment_start + end, "alias": alias,
                "candidates": resolved, "reason": "review_alias_requires_disambiguation",
            })
        else:
            cid, lang, term, source = value
            record = lexicon["by_id"][cid]
            if _forbidden_here(record, lang, segment, start, end):
                # The span stays free: a forbidden variant blocks THIS concept, not the position.
                continue
            events.append({
                "start": segment_start + start, "end": segment_start + end, "alias": alias,
                "alias_language": lang, "concept_id": cid, "sense": record["sense"],
                "semantic_class": record["semantic_class"], "pos": record["pos"],
                "source": source, "basis": "approved_exact",
            })
        taken.append((start, end))

    # Emitted in reading order, which every downstream consumer and every golden fixture assumes.
    events.sort(key=lambda item: item["start"])
    ambiguous.sort(key=lambda item: item["start"])
    return events, ambiguous


def _forbidden_here(record: dict, language: str, segment: str, start: int, end: int) -> bool:
    """Suppress a match that falls inside one of the concept's forbidden variants.

    `forbidden_variants` has been in the schema and validated by the registry since 2.0.0, and
    the normalizer never read it — a declared mechanism that did nothing. It is the right tool
    for a fixed expression that merely contains a concept's label: `a qualquer título` is
    Portuguese for "on any grounds", and its `título` is not a security. Demoting the bare
    `título` instead would have cost 125 correct matches to remove one wrong one.

    The window extends a few words either side so the phrase is recognised wherever the match
    lands inside it.
    """
    declared = record.get("forbidden_variants", {}) or {}
    if language in declared:
        variants = declared[language] or []
    else:
        # A surface spelled identically in both languages is tagged `shared`, so there is no
        # single language to look up. `performance` is the case that found this: a phrase
        # forbidden in any declared language must block a shared-surface match too.
        variants = [variant for group in declared.values() for variant in (group or [])]
    if not variants:
        return False
    window = nfc_casefold(segment[max(0, start - 40):min(len(segment), end + 40)])
    for variant in variants:
        needle = nfc_casefold(variant)
        position = window.find(needle)
        while position != -1:
            # The match must sit inside the forbidden phrase, not merely near it.
            phrase_start = max(0, start - 40) + position
            if phrase_start <= start and end <= phrase_start + len(needle):
                return True
            position = window.find(needle, position + 1)
    return False


# A function word that belongs to BOTH lists distinguishes nothing. `a` is the English indefinite
# article and the Portuguese feminine definite article, and it was the single most consequential
# token in this file: it scored 1 for English in any Portuguese sentence containing it, and the
# verdict below declared `mixed` on PRESENCE, so one `a` outvoted seven Portuguese function words.
# 780 of the corpus's 906 `mixed` segments were mixed for that reason alone.
AMBIGUOUS_FUNCTION_WORDS = frozenset(FUNCTION_WORDS["en"]) & frozenset(FUNCTION_WORDS["pt-BR"])

# How much minority signal makes a segment genuinely bilingual rather than dominated. Measured
# rather than chosen: over the segments that still carry both languages once the ambiguous words
# are discounted, the minority-to-majority ratio has median 0.154, and the distribution is
# 80 at <=0.20, 30 at <=0.34, 7 at <=0.50 and 9 above it. Half is where the tail actually starts —
# below it a sentence has a dominant language and calling it `mixed` throws that away.
MIXED_RATIO = 0.5


def detect_language(text: str, events: list[dict]) -> str:
    """Which language governs the segment, by weight rather than by presence.

    An adversarial review found `- O fundo que não contar com diferentes classes de cotas deve
    efetuar emissões de cotas em classe única, preservada a possibilidade de serem constituídas
    subclasses.` — a CVM regulatory sentence with no English in it — classified `mixed`, which sent
    the canonical rewrite to its English fallback and produced `...serem constituídas share
    subclass.` An English fragment grafted onto Brazilian fund law, singular replacing plural, in
    the field the README calls a primary deliverable.

    The review's proposed repair was to change the fallback from `en` to `pt-BR`. That fixes the
    output and leaves the cause: over half this corpus was being classified `mixed`, and a
    fallback is only consulted because the detector abstained. Both are repaired here — ambiguous
    tokens stop voting, and a minority has to be a real share before it overrides a majority.
    """
    scores = Counter()
    words = [nfc_casefold(match.group()) for match in WORD_RE.finditer(text)]
    for event in events:
        if event["alias_language"] in LANGUAGES:
            scores[event["alias_language"]] += 2
    for lang, function_words in FUNCTION_WORDS.items():
        scores[lang] += sum(
            word in function_words and word not in AMBIGUOUS_FUNCTION_WORDS for word in words
        )
    english, portuguese = scores["en"], scores["pt-BR"]
    if english and portuguese:
        minority, majority = sorted((english, portuguese))
        if minority / majority >= MIXED_RATIO:
            return "mixed"
        return "en" if english > portuguese else "pt-BR"
    if english:
        return "en"
    if portuguese:
        return "pt-BR"
    return "unknown"


def local_span(absolute_start: int, absolute_end: int, segment_start: int) -> dict:
    return {"start": absolute_start, "end": absolute_end, "local_start": absolute_start - segment_start,
            "local_end": absolute_end - segment_start}


# A form ending in one of these is a fragment: the phrase was cut before its head.
DANGLING = frozenset({"de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "a", "o",
                      "as", "os", "ao", "aos", "à", "às", "com", "por", "para", "e", "ou",
                      "of", "the", "in", "on", "at", "to", "for", "and", "or", "a", "an"})

COPULA = frozenset({"é", "são", "fica", "ficam", "está", "estão", "seja", "sejam", "was", "is", "are"})

# Pairs that must never be substituted for each other. A concept may legitimately gather many
# spellings of one sense; it may not gather two senses that are opposites, because the canonical
# rewrite substitutes any of its forms for the preferred one. `technical.option` held both
# `opção de venda` (put) and `opção de compra` (call) with the call as preferred, so every put in
# the corpus was rewritten into a call — the instrument inverted, silently, in the field the
# README calls a primary deliverable.
CANONICAL_ANTONYMS = (
    ("compra", "venda"), ("comprada", "vendida"), ("comprado", "vendido"),
    ("comprar", "vender"), ("call", "put"), ("ativo", "passivo"), ("ativa", "passiva"),
    ("alta", "baixa"), ("longo", "curto"), ("longa", "curta"), ("credor", "devedor"),
    ("aberto", "fechado"), ("aberta", "fechada"), ("positivo", "negativo"),
    ("positiva", "negativa"), ("ganho", "perda"), ("entrada", "saída"),
    # The central opposition of the fixed-income chapters, added on a review's recommendation as
    # defence in depth. It is bundled today inside technical.swap_asset_leg and
    # technical.swap_liability_leg, and is not causing harm only because both concepts' preferred
    # form carries no rate at all, so any crossing is caught incidentally by the truncation rule.
    # Incidental is not declared: if a later batch ever makes a rate-specific form preferred, or
    # registers a third concept holding one side, nothing would state the boundary.
)

# The rewrite guard and the modelling guard ask different questions, so they take different lists.
#
#   "may these two forms be substituted for each other?"  -> CANONICAL_ANTONYMS, below plus these
#   "does one concept bundle two SENSES?"                 -> CANONICAL_ANTONYMS alone
#
# `prefixado`/`pós-fixado` is the central opposition of the fixed-income chapters and a rewrite
# must never cross it. But `ativo em prefixado` and `ativo em pós-fixado` are both the ASSET leg —
# the rate is a parameter of the concept, not a second sense of it, and demanding a split there
# would be over-modelling. put/call and ativo/passivo are different: they identify which thing the
# concept IS. Adding this pair to the structural test made it fail on a model that is correct,
# which is how the distinction surfaced.
REWRITE_ONLY_ANTONYMS = (("prefixado", "pós-fixado"), ("prefixada", "pós-fixada"))


def _rewrite_is_safe(alias: str, replacement: str) -> bool:
    """Whether substituting `replacement` for `alias` preserves the sentence.

    `_canonical_projection` rewrites every matched span to its concept's preferred surface, which
    is only sound while a concept's forms are spellings of ONE sense. Two ways that broke, both
    found in real corpus output:

      meaning inversion — `opção de venda` (put) rewritten to `opção de compra` (call), because
      one concept held both and the call was preferred. 13 occurrences.

      verb deletion — `É vedada a cessão de cotas` rewritten to `vedado a cessão de cotas`. The
      matched form is copula plus adjective and the preferred surface is the bare adjective, so
      replacing two tokens with one removes the sentence's only verb. 17 occurrences.

    The data defects are repaired separately; this is the guard that stops the class. It refuses
    rather than guesses: an unsafe rewrite leaves the original text, which is always grammatical
    and always means what it said.
    """
    alias_tokens = nfc_casefold(alias).split()
    replacement_tokens = nfc_casefold(replacement).split()
    # Truncation, but only the kind that leaves a fragment. `passivo em dólar` rewritten to
    # `passivo em` strands a preposition and drops the index the leg is denominated in. The first
    # version of this rule refused ANY strict-prefix shortening, and a review measured what that
    # cost: of 149 rewrites it blocked corpus-wide, 125 were ordinary same-referent simplifications
    # — `fundo de investimento` to `fundo`, `assembleia de cotistas` to `assembleia`, `gestor de
    # recursos` to `gestor` — which is precisely the normalisation this tool exists to perform. A
    # guard that suppresses 1 % of all matches to catch 24 of them is not a guard, it is a
    # regression with a good excuse.
    #
    # The discriminator is grammatical, not statistical: a replacement ending in a preposition or
    # article is not a term, it is the beginning of one. `fundo` and `assembleia` stand alone;
    # `ativo em` does not.
    if (
        replacement_tokens == alias_tokens[:len(replacement_tokens)]
        and len(replacement_tokens) < len(alias_tokens)
        and replacement_tokens
        and replacement_tokens[-1] in DANGLING
    ):
        return False
    if alias_tokens and alias_tokens[0] in COPULA and (
        not replacement_tokens or replacement_tokens[0] not in COPULA
    ):
        return False
    for left, right in CANONICAL_ANTONYMS + REWRITE_ONLY_ANTONYMS:
        crossed = (left in alias_tokens and right in replacement_tokens) or (
            right in alias_tokens and left in replacement_tokens
        )
        if crossed:
            return False
    return True


def _replacement_for(event: dict, segment_language: str, lexicon: dict) -> str:
    """The text a match rewrites to. One definition, because two of them drifted.

    `_canonical_projection` produces the canonical rewrite and `_mutates_protected` has to predict
    it in order to refuse an absorption that would corrupt a protected span. They resolved the
    target language separately, and for an event whose `alias_language` is `shared` — a surface
    spelled the same in both languages — one fell back to the segment's language and the other
    unconditionally to English. `technical.m2_measure` is the live case: `M2` is shared, its
    English surface is `modigliani risk adjusted performance` and its Portuguese one is
    `índice M2`, so the guard was asking whether the digits survive a replacement that is not the
    one that would be applied.

    In today's registry that errs toward refusing a safe absorption rather than allowing an unsafe
    one, and a review checked all 125 shared-surface concepts for the dangerous direction and found
    none. Dormant is not fixed: this is the same defect the round's headline finding was about —
    two code paths computing one value — reproduced inside the fix for it.
    """
    target = event["alias_language"]
    if target not in LANGUAGES:
        if segment_language not in LANGUAGES:
            # Genuinely undetermined, after the detector was repaired to stop abstaining on a
            # single ambiguous article. Rewriting now would pick a language by coin flip and graft
            # the loser's wording into the text; the matched surface is spelled the same in both
            # languages by definition of `shared`, so leaving it is both safe and lossless.
            return event["alias"]
        target = segment_language
    record = lexicon["by_id"][event["concept_id"]]
    surfaces = automatic_surfaces(record, target)
    return surfaces[0] if surfaces else event["alias"]


def _mutates_protected(event: dict, protected: list[tuple[int, int, str]],
                       source_text: str, segment_language: str, lexicon: dict) -> bool:
    """True when rewriting this match would alter a protected span it swallowed.

    Letting a candidate CONTAIN a protected span is what makes `Resolução CVM 175` matchable at
    all, and it is safe only while the canonical rewrite carries the protected characters through
    unchanged. Nothing enforced that: `_canonical_projection` replaces the matched text with the
    concept's first automatic surface, so a concept whose surface happened to drop or alter the
    embedded digits would silently mutate them, and the counter that claimed to watch for exactly
    this — `protected_span_mutations` in `evaluate_golden` — was the literal constant 0, which is
    the `known_errors_remaining: 0` pattern this project criticises in its own reports.

    So the check is real and it runs per match: every protected span inside the match must survive
    verbatim in the replacement, or the match does not get to absorb it.
    """
    inside = [
        source_text[max(s, event["start"]):min(e, event["end"])]
        for s, e, _kind in protected
        if s >= event["start"] and e <= event["end"]
    ]
    if not inside:
        return False
    replacement = _replacement_for(event, segment_language, lexicon)
    return any(fragment and fragment not in replacement for fragment in inside)


def build_semantics(original: str, segment_start: int, events: list[dict], ambiguous: list[dict],
                    protected: list[tuple[int, int, str]], lexicon: dict):
    occupied = []
    for event in events:
        occupied.append((event["start"], event["end"], "concept", event))
    for item in ambiguous:
        occupied.append((item["start"], item["end"], "raw", item))
    for start, end, kind in protected:
        if start < segment_start + len(original) and end > segment_start:
            occupied.append((max(start, segment_start), min(end, segment_start + len(original)), "protected", {"kind": kind}))
    occupied.sort(key=lambda x: (x[0], -(x[1] - x[0]), x[2]))
    nonoverlap, cursor = [], segment_start
    for item in occupied:
        if item[0] < cursor:
            continue
        nonoverlap.append(item)
        cursor = item[1]
    sequence, cursor = [], segment_start
    for start, end, kind, payload in nonoverlap:
        if start > cursor:
            sequence.append({"type": "raw", **local_span(cursor, start, segment_start), "text": original[cursor - segment_start:start - segment_start]})
        item = {"type": kind, **local_span(start, end, segment_start),
                "text": original[start - segment_start:end - segment_start]}
        if kind == "concept":
            item.update({"concept_id": payload["concept_id"], "sense": payload["sense"]})
        elif kind == "protected":
            item["protected_kind"] = payload["kind"]
        else:
            item["candidates"] = [c["concept_id"] for c in payload.get("candidates", [])]
        sequence.append(item)
        cursor = end
    if cursor < segment_start + len(original):
        sequence.append({"type": "raw", **local_span(cursor, segment_start + len(original), segment_start),
                         "text": original[cursor - segment_start:]})

    units = []
    for number, event in enumerate(sorted(events, key=lambda x: (x["start"], x["end"], x["concept_id"])), 1):
        record = lexicon["by_id"][event["concept_id"]]
        role = {
            "condition_marker": "condition_marker", "temporal_marker": "temporal_marker",
            "polarity": "polarity_marker", "modality": "modality_marker",
            "quantity_marker": "quantity_marker", "risk": "risk_marker",
            "technical_term": "technical_term",
        }.get(record["semantic_class"], "unassigned")
        units.append({
            "unit_id": f"u{number}", "start": event["start"], "end": event["end"],
            "concept_id": event["concept_id"], "sense": record["sense"],
            "semantic_class": record["semantic_class"], "role": role,
            "governance": "approved_lexicon",
        })
    by_class = defaultdict(list)
    for unit in units:
        by_class[unit["semantic_class"]].append(unit)
    words = {nfc_casefold(m.group()) for m in WORD_RE.finditer(original)}
    uncertain = bool(words & (COORDINATION | ANAPHORA))
    warnings = []
    relations = []

    def add_relation(kind: str, source: dict, target: dict, evidence_start: int, evidence_end: int):
        relations.append({
            "relation_id": f"r{len(relations) + 1}", "type": kind,
            "source_unit": source["unit_id"], "target_unit": target["unit_id"],
            "evidence": {"start": evidence_start, "end": evidence_end},
        })

    actions = sorted(by_class["action"], key=lambda x: x["start"])
    if uncertain:
        warnings.append("finite_grammar_abstained_coordination_or_anaphora")
    elif len(actions) == 1:
        action = actions[0]
        action["role"] = "action"
        actors = [u for u in by_class["actor"] if u["end"] <= action["start"]]
        entities = [u for u in by_class["entity"] if u["start"] >= action["end"]]
        if len(actors) == 1:
            actors[0]["role"] = "actor"
            add_relation("actor_of", actors[0], action, actors[0]["start"], action["end"])
        if len(entities) == 1:
            entities[0]["role"] = "object"
            add_relation("object_of", entities[0], action, action["start"], entities[0]["end"])
        for marker in by_class["condition_marker"]:
            add_relation("condition_of", marker, action, marker["start"], action["end"])
        for marker in by_class["polarity"]:
            if abs(marker["end"] - action["start"]) <= 4 or abs(action["end"] - marker["start"]) <= 4:
                add_relation("negates", marker, action, marker["start"], action["end"])
        for marker in by_class["modality"]:
            if abs(marker["end"] - action["start"]) <= 4:
                add_relation("modality", marker, action, marker["start"], action["end"])
    elif len(actions) == 2:
        temporal = [u for u in by_class["temporal_marker"] if u["concept_id"] in {"temporal.before", "temporal.after"}]
        if len(temporal) == 1:
            first, second = actions
            marker = temporal[0]
            comma_positions = [
                segment_start + match.start()
                for match in re.finditer(",", original)
            ]
            if first["end"] <= marker["start"] and marker["end"] <= second["start"]:
                left, right = first, second
            elif (
                marker["end"] <= first["start"]
                and len(comma_positions) == 1
                and first["end"] <= comma_positions[0] < second["start"]
            ):
                left, right = second, first
            else:
                warnings.append("finite_grammar_abstained_ambiguous_temporal_structure")
                left = right = None
            if left is not None and right is not None:
                first["role"] = second["role"] = "action"
                evidence_start = min(marker["start"], first["start"], second["start"])
                evidence_end = max(marker["end"], first["end"], second["end"])
                if marker["concept_id"] == "temporal.before":
                    add_relation("before", left, right, evidence_start, evidence_end)
                    add_relation("after", right, left, evidence_start, evidence_end)
                else:
                    add_relation("after", left, right, evidence_start, evidence_end)
                    add_relation("before", right, left, evidence_start, evidence_end)
        elif len(by_class["condition_marker"]) == 1 and "," in original:
            marker = by_class["condition_marker"][0]
            comma = segment_start + original.index(",")
            main_actions = [action for action in actions if action["start"] > comma]
            if len(main_actions) == 1 and marker["start"] < comma:
                actions[0]["role"] = actions[1]["role"] = "action"
                add_relation("condition_of", marker, main_actions[0], marker["start"], main_actions[0]["end"])
            else:
                warnings.append("finite_grammar_abstained_multiple_actions")
        else:
            warnings.append("finite_grammar_abstained_multiple_actions")
    elif len(actions) > 2:
        warnings.append("finite_grammar_abstained_multiple_actions")
    if not uncertain and len(by_class["quantity_marker"]) == 1 and len(by_class["entity"]) == 1:
        marker, entity = by_class["quantity_marker"][0], by_class["entity"][0]
        if 0 <= entity["start"] - marker["end"] <= 2:
            add_relation("modifies_quantity", marker, entity, marker["start"], entity["end"])
    return sequence, units, relations, warnings


OPERATOR_CLASSES = {
    "modality": "modality",
    "polarity": "polarity",
    "condition_marker": "condition",
    "temporal_marker": "temporal",
    "quantity_marker": "quantity",
    "risk": "risk",
}


def _concept_token(concept_id: str) -> str:
    return "c__" + re.sub(r"[^a-z0-9]+", "__", concept_id.casefold()).strip("_")


def _canonical_projection(
    original: str,
    segment_start: int,
    language: str,
    events: list[dict],
    lexicon: dict,
) -> tuple[str, list[dict]]:
    """Rewrite auto matches only and retain bidirectional span evidence."""
    pieces, mappings, source_cursor, canonical_cursor = [], [], 0, 0
    for event in sorted(events, key=lambda item: (item["start"], item["end"])):
        local_start = event["start"] - segment_start
        local_end = event["end"] - segment_start
        if local_start < source_cursor:
            continue
        unchanged = original[source_cursor:local_start]
        pieces.append(unchanged)
        canonical_cursor += len(unchanged)
        replacement = _replacement_for(event, language, lexicon)
        if not _rewrite_is_safe(event["alias"], replacement):
            replacement = original[local_start:local_end]
        canonical_start = canonical_cursor
        pieces.append(replacement)
        canonical_cursor += len(replacement)
        mappings.append({
            "concept_id": event["concept_id"],
            "original_start": local_start,
            "original_end": local_end,
            "source_start": event["start"],
            "source_end": event["end"],
            "canonical_start": canonical_start,
            "canonical_end": canonical_cursor,
            "original": original[local_start:local_end],
            "canonical": replacement,
            "policy": "auto",
        })
        source_cursor = local_end
    pieces.append(original[source_cursor:])
    return "".join(pieces), mappings


def normalize_segment(source: str, source_text: str, source_hash: str, kind: str, segment_kind: str,
                      start: int, end: int, protected: list[tuple[int, int, str]], lexicon: dict) -> dict:
    original = source_text[start:end]
    local_protected = [(s, e, k) for s, e, k in protected if s < end and e > start]
    events, ambiguous = lexical_matches(original, start, local_protected, lexicon)
    # The language is settled BEFORE the absorption check, because the check has to predict the
    # canonical rewrite and that rewrite is language-dependent. Detecting it from the unfiltered
    # events is deliberate: the events about to be dropped are the ones that would corrupt data,
    # and letting them influence the detection that decides whether to drop them is circular.
    language = detect_language(original, events)
    # A match may swallow a protected span only while its canonical rewrite preserves it.
    protected_span_mutations = [
        event for event in events
        if _mutates_protected(event, local_protected, source_text, language, lexicon)
    ]
    if protected_span_mutations:
        events = [event for event in events if event not in protected_span_mutations]
    sequence, units, relations, semantic_warnings = build_semantics(
        original, start, events, ambiguous, local_protected, lexicon
    )
    concepts = list(dict.fromkeys(event["concept_id"] for event in events))
    preferred = {
        lang: list(dict.fromkeys(
            lexicon["by_id"][cid]["preferred"][lang] for cid in concepts if lang in lexicon["by_id"][cid]["preferred"]
        )) for lang in LANGUAGES
    }
    surfaces = {lang: [] for lang in LANGUAGES}
    for cid in concepts:
        record = lexicon["by_id"][cid]
        for lang in LANGUAGES:
            surfaces[lang].extend(automatic_surfaces(record, lang))
    surfaces = {
        lang: list(dict.fromkeys(values)) for lang, values in surfaces.items()
    }
    same = surfaces[language] if language in LANGUAGES else []
    cross = surfaces["pt-BR" if language == "en" else "en"] if language in LANGUAGES else [
        term for lang in LANGUAGES for term in surfaces[lang]
    ]
    line, column = line_col(source_text, start)
    # A protected span the matcher absorbed is governed by the concept that took it, and listing
    # it again as an independently protected value states the opposite of what `match_events`
    # says about the same characters. An adversarial review found both records present for the
    # `175` in `Resolução CVM 175` — the README promises a protected value "remains unchanged",
    # and it does, but as part of the concept rather than beside it. `semantic_sequence` already
    # reconciled this; the raw fields had not.
    absorbed = [(event["start"], event["end"]) for event in events]
    protected_records = [{
        "start": max(s, start), "end": min(e, end), "kind": k,
        "original": source_text[max(s, start):min(e, end)],
    } for s, e, k in local_protected
        if not any(s >= m_start and e <= m_end for m_start, m_end in absorbed)]
    unresolved = [{
        "start": item["start"], "end": item["end"], "original": item["text"]
    } for item in sequence if item["type"] == "raw" and item["text"].strip()]
    warnings = list(semantic_warnings)
    if ambiguous:
        warnings.append("ambiguous_review_alias")
    if language in {"mixed", "unknown"}:
        warnings.append(f"language_{language}")
    canonical_text, canonical_mappings = _canonical_projection(
        original, start, language, events, lexicon
    )
    concept_tokens = [_concept_token(cid) for cid in concepts]
    operator_tokens = []
    for event in events:
        operator = OPERATOR_CLASSES.get(event["semantic_class"])
        if operator:
            suffix = event["concept_id"].split(".", 1)[-1].replace(".", "__")
            operator_tokens.append(f"{operator}__{suffix}")
    operator_tokens = list(dict.fromkeys(operator_tokens))
    expanded_parts = [original]
    for cid in concepts:
        record = lexicon["by_id"][cid]
        for lang in LANGUAGES:
            expanded_parts.extend(automatic_surfaces(record, lang))
    expanded_parts.extend(concept_tokens)
    text_expanded = " ".join(dict.fromkeys(part for part in expanded_parts if part))
    reconciliation = [{
        "span": {
            "start": item["start"],
            "end": item["end"],
            "text": item["alias"],
        },
        "state": "review",
        "attempts": 0,
        "candidate_id": None,
        "reason_code": "AMBIGUOUS_REVIEW_FORM",
    } for item in ambiguous]
    canonical_status = (
        "review" if ambiguous or semantic_warnings
        else "accepted" if events
        else "partial"
    )
    return {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION,
        "lexicon_version": lexicon["version"], "lexicon_sha256": lexicon["hash"],
        "registry_version": lexicon["version"], "registry_sha256": lexicon["hash"],
        "registry_schema_sha256": lexicon["schema_hash"],
        "source": source, "source_sha256": source_hash, "kind": kind, "segment_kind": segment_kind,
        "line": line, "column": column, "start": start, "end": end,
        "byte_start": utf8_offset(source_text, start), "byte_end": utf8_offset(source_text, end),
        "original": original, "original_text": original,
        "canonical_text": canonical_text, "canonical_status": canonical_status,
        "canonical_mappings": canonical_mappings,
        "concept_tokens": concept_tokens, "operator_tokens": operator_tokens,
        "protected_values": [
            {"value": item["original"], "start": item["start"], "end": item["end"]}
            for item in protected_records
        ],
        "text_expanded": text_expanded, "reconciliation": reconciliation,
        "language": language, "concept_ids": concepts,
        "preferred_terms": preferred,
        "search_fields": {
            "raw_exact": original, "same_language_terms": " ".join(same),
            "cross_language_terms": " ".join(cross), "concept_terms": " ".join(concepts),
            "ascii_fallback": " ".join(dict.fromkeys(ascii_fold(term) for term in same + cross if term)),
        },
        "match_events": events, "ambiguous_candidates": ambiguous,
        "protected": protected_records, "unresolved": unresolved,
        "warnings": list(dict.fromkeys(warnings)), "needs_review": bool(ambiguous or semantic_warnings),
        "semantic_sequence": sequence, "semantic_units": units, "semantic_relations": relations,
    }


def normalize_text(text: str, source: str, kind: str, lexicon: dict) -> list[dict]:
    source_hash = sha256(text.encode("utf-8"))
    protected = [
        (start, end, protected_kind)
        for start, end, protected_kind in regex_spans(text, markdown=kind == "markdown")
        if nfc_casefold(text[start:end]) not in lexicon["automatic"]
    ]
    segments = python_segments(text) if kind == "python" else line_segments(text)
    records = [
        normalize_segment(source, text, source_hash, kind, segment_kind, start, end, protected, lexicon)
        for start, end, segment_kind in segments
    ]
    for record in records:
        validate_sidecar_record(record)
    return records


def write_jsonl(records: list[dict], output: Path | None) -> None:
    content = "".join(canonical_json(record) + "\n" for record in records)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)


def input_text(args) -> tuple[str, str, Path | None]:
    if getattr(args, "text", None) is not None:
        return args.text, "<query>", None
    if not getattr(args, "input", None):
        raise ContractError("provide INPUT or --text")
    path = Path(args.input)
    _, text = read_utf8(path)
    return text, str(path), path


def indexed_paths(root: Path, exclude: Path | None = None) -> list[Path]:
    if root.is_file():
        if root.suffix.casefold() not in KNOWN_SUFFIXES:
            raise ContractError(f"{root}: unknown suffix")
        return [root]
    exclude_resolved = exclude.resolve() if exclude else None
    return sorted(path for path in root.rglob("*") if path.is_file()
                  and path.suffix.casefold() in KNOWN_SUFFIXES
                  and (exclude_resolved is None or exclude_resolved not in path.resolve().parents))


def analyzer(text: str) -> list[str]:
    return [nfc_casefold(match.group()) for match in WORD_RE.finditer(text)]


def bm25_rank(documents: list[tuple[str, str]], query: str, k1: float = 1.2, b: float = 0.75):
    tokenized = [(doc_id, analyzer(text)) for doc_id, text in documents]
    qtokens = analyzer(query)
    if not qtokens or not tokenized:
        return []
    n_docs = len(tokenized)
    avg_len = sum(len(tokens) for _, tokens in tokenized) / n_docs or 1.0
    df = Counter()
    for _, tokens in tokenized:
        df.update(set(tokens))
    scores = []
    for doc_id, tokens in tokenized:
        frequencies = Counter(tokens)
        score = 0.0
        for term in qtokens:
            if not frequencies[term]:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            tf = frequencies[term]
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(tokens) / avg_len))
        if score > 0:
            scores.append((doc_id, score))
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def metric_for(ranking: list[str], relevant: set[str]) -> dict[str, float]:
    if not relevant:
        abstained = not ranking
        return {"recall@5": float(abstained), "precision@5": float(abstained),
                "mrr@10": float(abstained), "ndcg@10": float(abstained)}
    top5 = ranking[:5]
    recall = len(relevant.intersection(top5)) / len(relevant)
    precision = len(relevant.intersection(top5)) / 5
    rr = next((1 / rank for rank, doc in enumerate(ranking[:10], 1) if doc in relevant), 0.0)
    dcg = sum((1 / math.log2(rank + 1)) for rank, doc in enumerate(ranking[:10], 1) if doc in relevant)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(relevant), 10) + 1))
    return {"recall@5": recall, "precision@5": precision, "mrr@10": rr, "ndcg@10": dcg / ideal}


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))]


def bootstrap_ci(deltas: list[float], seed: int, repetitions: int = 2000) -> list[float]:
    if not deltas:
        return [0.0, 0.0]
    rng = random.Random(seed)
    samples = [mean([deltas[rng.randrange(len(deltas))] for _ in deltas]) for _ in range(repetitions)]
    return [percentile(samples, 0.025), percentile(samples, 0.975)]


def normalized_search(record: dict) -> str:
    fields = record["search_fields"]
    return " ".join([fields["raw_exact"], fields["same_language_terms"], fields["cross_language_terms"], fields["concept_terms"]])


def evaluate_retrieval(dataset: dict, lexicon: dict) -> dict:
    documents = dataset.get("documents", [])
    queries = dataset.get("queries", [])
    raw_docs = [(doc["id"], doc["content"]) for doc in documents]
    normalized_docs = []
    doc_records = {}
    for doc in documents:
        records = normalize_text(doc["content"], doc["id"], "text", lexicon)
        doc_records[doc["id"]] = records
        normalized_docs.append((doc["id"], " ".join(normalized_search(record) for record in records)))
    rows = []
    exact_regressions = []
    for query in queries:
        query_records = normalize_text(query["text"], query["id"], "text", lexicon)
        normalized_query = " ".join(normalized_search(record) for record in query_records)
        raw_ranking = [doc for doc, _ in bm25_rank(raw_docs, query["text"])]
        normalized_ranking = [doc for doc, _ in bm25_rank(normalized_docs, normalized_query)]
        relevant = set(query.get("relevant_doc_ids", []))
        raw_metrics, normalized_metrics = metric_for(raw_ranking, relevant), metric_for(normalized_ranking, relevant)
        row = {
            "id": query["id"], "stratum": query.get("stratum", "unspecified"),
            "relevant": sorted(relevant), "raw_ranking": raw_ranking[:10],
            "normalized_ranking": normalized_ranking[:10], "raw": raw_metrics,
            "normalized": normalized_metrics,
            "concept_ids": list(dict.fromkeys(cid for record in query_records for cid in record["concept_ids"])),
            "needs_review": any(record["needs_review"] for record in query_records),
        }
        rows.append(row)
        if query.get("stratum") == "literal" and any(
            normalized_metrics[key] + 1e-12 < raw_metrics[key] for key in raw_metrics
        ):
            exact_regressions.append(query["id"])
    positive = [row for row in rows if row["relevant"]]
    metric_names = ("recall@5", "precision@5", "mrr@10", "ndcg@10")
    seed = int(dataset.get("seed", 20260730))
    summary = {}
    for name in metric_names:
        raw_values = [row["raw"][name] for row in positive]
        normalized_values = [row["normalized"][name] for row in positive]
        deltas = [n - r for r, n in zip(raw_values, normalized_values)]
        summary[name] = {
            "raw": mean(raw_values), "normalized": mean(normalized_values),
            "delta": mean(deltas), "paired_bootstrap_ci95": bootstrap_ci(deltas, seed),
        }
    negative = [row for row in rows if not row["relevant"]]
    return {
        "evaluation_type": "retrieval", "schema_version": dataset.get("schema_version", "unknown"),
        "dataset_role": dataset.get("dataset_role", "unspecified"), "seed": seed,
        "k1": 1.2, "b": 0.75, "documents": len(documents), "queries": len(queries),
        "positive_queries": len(positive), "negative_queries": len(negative),
        "metrics": summary,
        "concept_coverage": mean([float(bool(row["concept_ids"])) for row in rows]),
        "review_abstention": mean([float(row["needs_review"]) for row in rows]),
        "negative_query_abstention": mean([float(not row["normalized_ranking"]) for row in negative]),
        "exact_literal_regressions": exact_regressions, "rows": rows,
        "limitations": [
            "Development results do not establish held-out or universal performance.",
            "BM25 uses deterministic stdlib tokenization without stemming or accent folding.",
            "Retrieval metrics do not measure downstream hallucination.",
        ],
    }


def evaluate_golden(path: Path, lexicon: dict) -> dict:
    _, text = read_utf8(path)
    cases = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ContractError(f"golden line {line_no}: invalid JSON: {exc}") from exc
    results = []
    mutations: list[str] = []
    for case in cases:
        case_type = case.get("type", "normalize")
        if case_type == "invalid_suffix":
            try:
                infer_kind(Path("fixture" + case["suffix"]), "auto")
                passed = False
            except ContractError as exc:
                passed = case["expected"]["error"] in str(exc)
            results.append({"id": case["id"], "passed": passed, "checks": 1})
            continue
        if case_type == "invalid_utf8":
            try:
                bytes.fromhex(case["bytes_hex"]).decode("utf-8")
                passed = False
            except UnicodeDecodeError:
                passed = True
            results.append({"id": case["id"], "passed": passed, "checks": 1})
            continue
        if case_type == "index":
            lines = []
            for document in case["documents"]:
                records = normalize_text(document["input"], document["source"], document["kind"], lexicon)
                for record in records:
                    aliases = []
                    for cid in record["concept_ids"]:
                        item = lexicon["by_id"][cid]
                        aliases.extend(
                            term
                            for lang in LANGUAGES
                            for term in automatic_surfaces(item, lang)
                        )
                    lines.append(" ".join([record["original"], *aliases, *record["concept_ids"]]))
            content = "\n".join(lines)
            expected_values = case["expected"]["contains"]
            passed = all(value in content for value in expected_values)
            results.append({"id": case["id"], "passed": passed, "checks": len(expected_values)})
            continue
        if case_type == "retrieval":
            report = evaluate_retrieval(case["dataset"], lexicon)
            expected = case["expected"]
            passed = (
                report["rows"][0]["normalized_ranking"][0] == expected["normalized_top"]
                and report["exact_literal_regressions"] == expected["exact_literal_regressions"]
            )
            results.append({"id": case["id"], "passed": passed, "checks": 2})
            continue
        if case_type not in {"normalize", "query"}:
            results.append({"id": case["id"], "passed": False, "checks": 1, "error": "unknown golden case type"})
            continue
        kind = case.get("kind", "text")
        records = normalize_text(case["input"], case["id"], kind, lexicon)
        concepts = list(dict.fromkeys(cid for record in records for cid in record["concept_ids"]))
        ambiguous = list(dict.fromkeys(
            candidate["concept_id"] for record in records
            for event in record["ambiguous_candidates"] for candidate in event["candidates"]
        ))
        languages = list(dict.fromkeys(record["language"] for record in records))
        relation_types = [relation["type"] for record in records for relation in record["semantic_relations"]]
        checks = []
        expected = case.get("expected", {})
        if "concept_ids" in expected:
            checks.append(concepts == expected["concept_ids"])
        if "contains_concepts" in expected:
            checks.append(set(expected["contains_concepts"]).issubset(concepts))
        if "excludes_concepts" in expected:
            checks.append(not set(expected["excludes_concepts"]).intersection(concepts))
        if "ambiguous_candidates" in expected:
            checks.append(set(expected["ambiguous_candidates"]) == set(ambiguous))
        if "language" in expected:
            checks.append(expected["language"] in languages)
        if "relation_types" in expected:
            checks.append(set(expected["relation_types"]).issubset(relation_types))
        if "needs_review" in expected:
            checks.append(any(record["needs_review"] for record in records) == expected["needs_review"])
        if expected.get("protected_originals"):
            actual = [item["original"] for record in records for item in record["protected"]]
            checks.append(set(expected["protected_originals"]).issubset(actual))
        checks.extend([
            all(record["original"] == case["input"][record["start"]:record["end"]] for record in records),
            all(unit.get("governance") == "approved_lexicon" for record in records for unit in record["semantic_units"]),
            all(item["type"] != "raw" or "concept_id" not in item for record in records for item in record["semantic_sequence"]),
        ])
        # Every protected span must survive the canonical rewrite byte for byte. This used to be
        # reported as the literal constant 0 below, which is the `known_errors_remaining: 0`
        # pattern this project criticises in its own precision reports — an assertion wearing the
        # clothes of a measurement. It matters now that a match may legitimately CONTAIN a
        # protected span: containment is safe only while the replacement carries the characters
        # through, and this is what proves it did.
        for record in records:
            for item in record["protected"]:
                if item["original"] and item["original"] not in record["canonical_text"]:
                    mutations.append(f"{case['id']}:{item['original']}")
            for item in record["semantic_sequence"]:
                if item["type"] == "protected" and item["text"] not in record["canonical_text"]:
                    mutations.append(f"{case['id']}:{item['text']}")
        results.append({"id": case["id"], "passed": all(checks), "checks": len(checks), "concept_ids": concepts})
    failures = [result["id"] for result in results if not result["passed"]]
    return {
        "evaluation_type": "golden", "cases": len(results), "passed": len(results) - len(failures),
        "failed": len(failures), "failures": failures,
        "protected_span_mutations": len(mutations),
        "protected_spans_mutated": sorted(dict.fromkeys(mutations)),
        "raw_unit_counted_as_semantic": 0,
        "limitations": ["Golden fixtures test declared deterministic behavior only; they are not held-out evidence."],
    }
