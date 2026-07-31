"""Zip-safe loading and fail-closed validation for registry v2."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from importlib import resources
from pathlib import Path
from typing import Any

LANGUAGES = ("en", "pt-BR")
REGISTRY_VERSION = "2.27.0"
CONCEPT_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class ContractError(ValueError):
    """A fail-closed public contract error."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nfc_casefold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def automatic_surfaces(record: dict, language: str) -> tuple[str, ...]:
    """Project deterministic retrieval surfaces from approved lexical forms only."""
    observed = {
        nfc_casefold(value) for value in record["labels"][language]["observed"]
    }
    return tuple(
        form["form"]
        for form in record["lexical_forms"][language]
        if form["policy"] == "auto" and nfc_casefold(form["form"]) not in observed
    )


def package_data(name: str):
    """Return a Traversable so default loading also works from a wheel/zip."""
    return resources.files("semantic_normalizer.data").joinpath(name)


def _read_bytes(source: str | Path | Any | None, default_name: str) -> tuple[bytes, str]:
    target = package_data(default_name) if source is None else source
    if isinstance(target, (str, Path)):
        path = Path(target)
        return path.read_bytes(), str(path)
    return target.read_bytes(), str(target)


def _require_keys(value: object, required: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: must be an object")
    if set(value) != required:
        raise ContractError(
            f"{where}: keys differ; missing={sorted(required - set(value))} "
            f"extra={sorted(set(value) - required)}"
        )
    return value


def _term(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 320:
        raise ContractError(f"{where}: invalid non-empty string")
    return value


def _term_list(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or len(value) != len(set(value)):
        raise ContractError(f"{where}: must be a unique string list")
    return [_term(item, where) for item in value]


RECORD_FIELDS = {
    "concept_id", "definition", "semantic_class", "domains", "pos", "labels",
    "lexical_forms", "relations", "forbidden_variants", "contexts", "authority",
    "source", "positive_examples", "negative_examples", "status",
    "governed_technical_term", "registry_version",
}
LABEL_FIELDS = {"pref", "alt", "hidden", "observed"}
RELATION_FIELDS = {"broader", "narrower", "related"}
FORM_FIELDS = {"form", "features", "policy"}


def validate_record(record: object, line_no: int, schema: dict) -> dict:
    """Validate one record against the canonical schema's closed shape."""
    item = _require_keys(record, RECORD_FIELDS, f"registry line {line_no}")
    schema_fields = set(schema.get("required", ()))
    schema_properties = set(schema.get("properties", ()))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ContractError("registry schema is not Draft 2020-12")
    if schema.get("additionalProperties") is not False:
        raise ContractError("registry schema must fail closed")
    if schema_fields != RECORD_FIELDS or schema_properties != RECORD_FIELDS:
        raise ContractError("registry schema and runtime record fields diverge")
    cid = item["concept_id"]
    if not isinstance(cid, str) or not CONCEPT_ID_RE.fullmatch(cid):
        raise ContractError(f"registry line {line_no}: invalid concept_id")
    if item["registry_version"] != REGISTRY_VERSION:
        raise ContractError(f"registry line {line_no}: unsupported registry_version")
    for field in ("semantic_class", "pos"):
        allowed = set(schema["properties"][field].get("enum", ()))
        if item[field] not in allowed:
            raise ContractError(f"registry line {line_no}: invalid {field}")
    _term(item["definition"], f"{cid}.definition")
    _term(item["authority"], f"{cid}.authority")
    _term(item["source"], f"{cid}.source")
    if item["status"] not in {"approved", "deprecated"}:
        raise ContractError(f"{cid}: invalid status")
    if not isinstance(item["governed_technical_term"], bool):
        raise ContractError(f"{cid}: governed_technical_term must be boolean")
    for field in ("domains", "contexts"):
        values = _term_list(item[field], f"{cid}.{field}")
        if not values:
            raise ContractError(f"{cid}.{field}: cannot be empty")
    labels = _require_keys(item["labels"], set(LANGUAGES), f"{cid}.labels")
    lexical_forms = _require_keys(
        item["lexical_forms"], set(LANGUAGES), f"{cid}.lexical_forms"
    )
    forbidden = _require_keys(
        item["forbidden_variants"], set(LANGUAGES), f"{cid}.forbidden_variants"
    )
    for language in LANGUAGES:
        label_set = _require_keys(
            labels[language], LABEL_FIELDS, f"{cid}.labels.{language}"
        )
        pref = _term(label_set["pref"], f"{cid}.labels.{language}.pref")
        alt = set(_term_list(label_set["alt"], f"{cid}.labels.{language}.alt"))
        hidden = set(
            _term_list(label_set["hidden"], f"{cid}.labels.{language}.hidden")
        )
        observed = set(
            _term_list(label_set["observed"], f"{cid}.labels.{language}.observed")
        )
        if pref in alt or pref in hidden or alt & hidden:
            raise ContractError(f"{cid}.{language}: SKOS S13 label overlap")
        if not isinstance(lexical_forms[language], list) or not lexical_forms[language]:
            raise ContractError(f"{cid}.lexical_forms.{language}: cannot be empty")
        auto_forms, review_forms = set(), set()
        lexical_surfaces: set[str] = set()
        normalized_surfaces: dict[str, tuple[str, str]] = {}
        for number, form in enumerate(lexical_forms[language], 1):
            entry = _require_keys(
                form, FORM_FIELDS, f"{cid}.lexical_forms.{language}[{number}]"
            )
            surface = _term(entry["form"], f"{cid}.lexical_forms.{language}.form")
            if not isinstance(entry["features"], dict):
                raise ContractError(f"{cid}.{language}.{surface}: features must be object")
            feature_schema = schema["$defs"]["features"]
            if (
                feature_schema.get("additionalProperties") is not False
                or not set(entry["features"]).issubset(feature_schema["properties"])
            ):
                raise ContractError(f"{cid}.{language}.{surface}: invalid feature keys")
            if entry["policy"] == "auto":
                auto_forms.add(surface)
            elif entry["policy"] == "review":
                review_forms.add(surface)
            else:
                raise ContractError(f"{cid}.{language}.{surface}: invalid policy")
            normalized = nfc_casefold(surface)
            if normalized in normalized_surfaces:
                previous_surface, previous_policy = normalized_surfaces[normalized]
                raise ContractError(
                    f"{cid}.{language}: duplicate lexical form {surface!r}; "
                    f"already declared as {previous_surface!r} with policy "
                    f"{previous_policy!r}"
                )
            normalized_surfaces[normalized] = (surface, entry["policy"])
            lexical_surfaces.add(surface)
        label_surfaces = {pref} | alt | hidden | observed
        if lexical_surfaces != label_surfaces:
            raise ContractError(
                f"{cid}.{language}: lexical forms and label surfaces differ"
            )
        if observed - review_forms:
            raise ContractError(
                f"{cid}.{language}: observed labels must use review policy"
            )
        if auto_forms & review_forms:
            raise ContractError(
                f"{cid}.{language}: a lexical form cannot use both policies"
            )
        _term_list(forbidden[language], f"{cid}.forbidden_variants.{language}")
    relations = _require_keys(item["relations"], RELATION_FIELDS, f"{cid}.relations")
    for relation, targets in relations.items():
        for target in _term_list(targets, f"{cid}.relations.{relation}"):
            if not CONCEPT_ID_RE.fullmatch(target):
                raise ContractError(f"{cid}: invalid relation target {target!r}")
    for field in ("positive_examples", "negative_examples"):
        examples = _require_keys(item[field], set(LANGUAGES), f"{cid}.{field}")
        for language in LANGUAGES:
            if not _term_list(examples[language], f"{cid}.{field}.{language}"):
                raise ContractError(f"{cid}.{field}.{language}: cannot be empty")
    return item


def load_registry(
    registry: str | Path | Any | None = None,
    schema: str | Path | Any | None = None,
) -> dict:
    from .schema_validation import validate_instance

    registry_bytes, registry_origin = _read_bytes(registry, "registry.jsonl")
    schema_bytes, schema_origin = _read_bytes(schema, "registry.schema.json")
    try:
        schema_doc = json.loads(schema_bytes)
    except json.JSONDecodeError as exc:
        raise ContractError(f"{schema_origin}: invalid JSON schema: {exc}") from exc
    records = []
    for line_no, line in enumerate(registry_bytes.decode("utf-8").splitlines(), 1):
        if not line.strip():
            raise ContractError(f"registry line {line_no}: blank JSONL record")
        try:
            parsed = json.loads(line)
            validate_instance(parsed, schema_doc, path=f"registry line {line_no}")
            records.append(validate_record(parsed, line_no, schema_doc))
        except json.JSONDecodeError as exc:
            raise ContractError(f"registry line {line_no}: invalid JSON: {exc}") from exc
    if not records:
        raise ContractError("registry is empty")
    ids = [record["concept_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ContractError("duplicate concept_id")
    by_id = {record["concept_id"]: record for record in records}
    automatic: dict[str, tuple[str, str, str, str]] = {}
    reviews: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for record in records:
        cid = record["concept_id"]
        for language in LANGUAGES:
            labels = record["labels"][language]
            auto_surface_keys = {
                nfc_casefold(surface)
                for surface in automatic_surfaces(record, language)
            }
            for form in record["lexical_forms"][language]:
                surface, policy = form["form"], form["policy"]
                key = nfc_casefold(surface)
                if key not in auto_surface_keys:
                    reviews[key].append((cid, language, surface, "review"))
                    continue
                source = (
                    "preferred" if surface == labels["pref"]
                    else "hidden" if surface in labels["hidden"]
                    else "alt"
                )
                value = (cid, language, surface, source)
                if key in automatic and automatic[key][0] != cid:
                    raise ContractError(
                        f"automatic form collision {surface!r}: "
                        f"{automatic[key][0]} vs {cid}"
                    )
                if key in automatic and automatic[key][1] != language:
                    automatic[key] = (cid, "shared", surface, source)
                else:
                    automatic[key] = value
    for record in records:
        cid = record["concept_id"]
        for target in record["relations"]["broader"]:
            if target not in by_id or cid not in by_id[target]["relations"]["narrower"]:
                raise ContractError(f"{cid}: broader/narrower inverse is invalid")
        for target in record["relations"]["narrower"]:
            if target not in by_id or cid not in by_id[target]["relations"]["broader"]:
                raise ContractError(f"{cid}: narrower/broader inverse is invalid")
        for target in record["relations"]["related"]:
            if target not in by_id or cid not in by_id[target]["relations"]["related"]:
                raise ContractError(f"{cid}: related relation is not symmetric")

    # Compatibility view for the existing deterministic parser. Authority stays
    # in canonical v2 records; these derived aliases are never serialized.
    runtime_records = []
    for record in records:
        view = dict(record)
        view["sense"] = record["definition"]
        view["preferred"] = {
            language: record["labels"][language]["pref"] for language in LANGUAGES
        }
        view["automatic_surfaces"] = {
            language: list(automatic_surfaces(record, language))
            for language in LANGUAGES
        }
        view["auto_aliases"] = {
            language: [
                surface
                for surface in automatic_surfaces(record, language)
                if surface != record["labels"][language]["pref"]
            ]
            for language in LANGUAGES
        }
        view["review_aliases"] = {
            language: [
                form["form"]
                for form in record["lexical_forms"][language]
                if form["policy"] == "review"
            ]
            for language in LANGUAGES
        }
        runtime_records.append(view)
    runtime_by_id = {record["concept_id"]: record for record in runtime_records}
    return {
        "path": registry_origin,
        "schema_path": schema_origin,
        "hash": sha256(registry_bytes),
        "schema_hash": sha256(schema_bytes),
        "version": REGISTRY_VERSION,
        "records": runtime_records,
        "canonical_records": records,
        "by_id": runtime_by_id,
        "automatic": automatic,
        "reviews": reviews,
    }


load_lexicon = load_registry


def init_workspace(
    directory: str | Path,
    registry: str | Path | Any | None = None,
    schema: str | Path | Any | None = None,
) -> dict:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    registry_bytes, _ = _read_bytes(registry, "registry.jsonl")
    schema_bytes, _ = _read_bytes(schema, "registry.schema.json")
    files = {
        "registry.jsonl": registry_bytes,
        "registry.schema.json": schema_bytes,
        "candidates.jsonl": b"",
        "decisions.jsonl": b"",
    }
    existing = [target / name for name in files if (target / name).exists()]
    if existing:
        raise ContractError(f"workspace target already exists: {existing[0]}")
    for name, content in files.items():
        path = target / name
        path.write_bytes(content)
    return {
        "workspace": str(target),
        "registry_sha256": sha256(registry_bytes),
        "schema_sha256": sha256(schema_bytes),
        "files": sorted(files),
    }
