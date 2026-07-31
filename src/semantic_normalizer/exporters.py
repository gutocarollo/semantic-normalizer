"""Deterministic registry exports."""

from __future__ import annotations

import json

from .registry import LANGUAGES, automatic_surfaces

SKOS = "http://www.w3.org/2004/02/skos/core#"


def _literal(value: str, language: str | None = None) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\n", "\\n").replace("\r", "\\r")
    )
    return f'"{escaped}"' + (f"@{language}" if language else "")


def _iri(concept_id: str, base_iri: str) -> str:
    return f"<{base_iri.rstrip('/')}/{concept_id}>"


def export_skos(registry: dict, base_iri: str = "https://cga-game.local/concept") -> str:
    lines = []
    for record in sorted(registry["canonical_records"], key=lambda item: item["concept_id"]):
        subject = _iri(record["concept_id"], base_iri)
        lines.append(f"{subject} <{SKOS}definition> {_literal(record['definition'])} .")
        for language in LANGUAGES:
            labels = record["labels"][language]
            lines.append(f"{subject} <{SKOS}prefLabel> {_literal(labels['pref'], language)} .")
            for label in sorted(labels["alt"]):
                lines.append(f"{subject} <{SKOS}altLabel> {_literal(label, language)} .")
            for label in sorted(labels["hidden"]):
                lines.append(f"{subject} <{SKOS}hiddenLabel> {_literal(label, language)} .")
        for relation, predicate in (
            ("broader", "broader"), ("narrower", "narrower"), ("related", "related")
        ):
            for target in sorted(record["relations"][relation]):
                lines.append(
                    f"{subject} <{SKOS}{predicate}> {_iri(target, base_iri)} ."
                )
    return "\n".join(sorted(lines)) + ("\n" if lines else "")


def export_synonym_graph(registry: dict) -> str:
    """Emit only approved automatic equivalences; exclude review and taxonomy."""
    rules = []
    for record in sorted(registry["canonical_records"], key=lambda item: item["concept_id"]):
        surfaces = [
            surface
            for language in LANGUAGES
            for surface in automatic_surfaces(record, language)
        ]
        unique = sorted(dict.fromkeys(surfaces), key=lambda value: (value.casefold(), value))
        if len(unique) > 1:
            rules.append(", ".join(unique))
    return "\n".join(rules) + ("\n" if rules else "")


def export_manifest(registry: dict) -> str:
    return json.dumps(
        {
            "registry_version": registry["version"],
            "registry_sha256": registry["hash"],
            "skos_format": "application/n-triples",
            "synonym_graph_policy": "approved-auto-equivalences-only",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
