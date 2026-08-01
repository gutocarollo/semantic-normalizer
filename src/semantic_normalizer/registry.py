"""Zip-safe loading and fail-closed validation for registry v2."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from importlib import resources
from pathlib import Path
from typing import Any

LANGUAGES = ("en", "pt-BR")
REGISTRY_VERSION = "2.44.0"
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
    """Project deterministic retrieval surfaces, PREFERRED LABEL FIRST.

    The canonical rewrite substitutes `automatic_surfaces(...)[0]`, and this used to return
    whatever happened to sit first in `lexical_forms` — insertion order, which is the order
    batches and amendments ran in. The registry declares a preferred label per language and the
    rewrite ignored it, so `entity.registration` with `pref: registro` canonicalised to
    `registro na CVM`, turning `o registro do fundo na CVM` into `o registro na CVM do fundo na
    CVM`. Renaming the label did not fix it, which is how the real cause surfaced: the label was
    never what the rewrite consulted.

    Eight concepts differ that way today. Ordering the projection by the declared label makes the
    canonical form the one the registry says it is, and leaves every other surface reachable
    behind it.
    """
    observed = {
        nfc_casefold(value) for value in record["labels"][language]["observed"]
    }
    surfaces = [
        form["form"]
        for form in record["lexical_forms"][language]
        if form["policy"] == "auto" and nfc_casefold(form["form"]) not in observed
    ]
    preferred = record["labels"][language]["pref"]
    if preferred in surfaces:
        surfaces.remove(preferred)
        surfaces.insert(0, preferred)
    return tuple(surfaces)


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
    contexts: Iterable[str] | None = None,
) -> dict:
    """Load the registry, optionally scoped to one or more domains.

    Every record carries a `contexts` list — `["finance", "cga"]`, `["documentation", "software"]` —
    and until now NOTHING read it except the schema validator. That is the exact shape this project
    has already been burned by twice: a declarative field no consumer reads is documentation, not a
    contract, and the defect only surfaces when someone tries to use the field to change behaviour
    and nothing changes.

    Scoping matters because 83 % of all matches come from bare single-word Portuguese forms (6,446
    of 7,780 events), and those are precisely the surfaces that mean different things in different
    domains: `ação` is a share here and a lawsuit in law and a drug's effect in medicine; `título`
    is a debt security here and a deed or a degree elsewhere; `fluxo` is cash flow here and blood
    flow in medicine. Merge two domains into one table and the collision demoter sends BOTH
    claimants to `review` — punishing the incumbent domain to admit the newcomer.

    So the fix is not one big table with a `contexts` column nobody reads. It is: validate the whole
    registry (integrity is global), then build the MATCHER's tables from the requested scope only.
    `contexts=None` keeps every record, so existing callers are unaffected.

        load_lexicon()                          # everything, as before
        load_lexicon(contexts=["cga"])          # only the CGA financial pack
        load_lexicon(contexts=["cga", "core"])  # pack plus shared operators

    Relations are still checked across the FULL record set, not the scope: a concept pointing at a
    broader term that the scope excludes is a fact about the registry and must not be hidden by the
    lens someone happened to load it through.
    """
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

    # The scope. Integrity above was checked over EVERY record; the matcher below sees only the
    # requested domains. Splitting it here is what makes `contexts` a contract instead of a label:
    # a form outside the scope is not demoted, not hidden, not ranked lower — it is simply not in
    # the table the matcher reads, so it cannot collide with anything in the active domain.
    scope = None
    if contexts is not None:
        scope = {str(name).casefold() for name in contexts}
        if not scope:
            raise ContractError("contexts was given but empty; pass None to load everything")
        selected = [
            record for record in records
            if scope & {str(name).casefold() for name in record.get("contexts", [])}
        ]
        if not selected:
            available = sorted({
                str(name).casefold()
                for record in records for name in record.get("contexts", [])
            })
            raise ContractError(
                f"no concept carries any of the requested contexts {sorted(scope)}. "
                f"The registry declares: {available}"
            )
        records = selected

    # Building the matcher table. The invariant is that a surface never resolves automatically
    # to two concepts — but WHERE that is a defect depends on scope, and conflating the two
    # cases is what would make domain packs impossible.
    #
    # Same scope (contexts intersect, or either side is global): a defect. The importer's
    # demoter should have caught it, and the ledger records the time it did not (`CDS`, batch
    # 24). Raise, exactly as before.
    #
    # Disjoint scopes in an UNSCOPED load: not a defect. `premise` is `entity.premise` in the
    # CGA pack and `reasoning.premise` in the reasoning pack, and no scoped table ever holds
    # both — `load_lexicon(contexts=["cga"])` and `load_lexicon(contexts=["reasoning"])` each
    # see exactly one. What `contexts=None` does is merge every domain, and in a merged view
    # that surface IS ambiguous. So it is demoted to review, the way an ambiguous surface
    # always is here, and both owners are reported under `cross_domain_ambiguous` rather than
    # silently dropped. Raising instead would mean adding any second pack breaks the default
    # load — plug-and-play that stops working the moment you plug the second thing in.
    concept_contexts = {
        record["concept_id"]: {str(name).casefold() for name in record.get("contexts", [])}
        for record in records
    }

    def _same_scope(first: str, second: str) -> bool:
        left, right = concept_contexts.get(first, set()), concept_contexts.get(second, set())
        return (not left) or (not right) or bool(left & right)

    automatic: dict[str, tuple[str, str, str, str]] = {}
    reviews: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    cross_domain_ambiguous: dict[str, list[str]] = {}
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
                if key in cross_domain_ambiguous:
                    # A third claimant must be checked against EVERY owner already collected,
                    # not waved through because the surface is already marked ambiguous. The
                    # first version appended blindly, and an adversarial review reproduced the
                    # consequence: with owners arriving as [cga.a, reasoning.c, cga.b], the
                    # same-scope pair `cga.a`/`cga.b` was silently demoted to review instead of
                    # raising, because `reasoning.c` had already opened the bucket. That is a
                    # registry defect wearing the costume of a legitimate cross-domain merge —
                    # precisely the failure this whole branch must not be able to produce.
                    for owner in cross_domain_ambiguous[key]:
                        if _same_scope(owner, cid):
                            raise ContractError(
                                f"automatic form collision {surface!r}: {owner} vs {cid}"
                            )
                    cross_domain_ambiguous[key].append(cid)
                    reviews[key].append((cid, language, surface, "review"))
                    continue
                if key in automatic and automatic[key][0] != cid:
                    incumbent = automatic[key][0]
                    if _same_scope(incumbent, cid):
                        raise ContractError(
                            f"automatic form collision {surface!r}: "
                            f"{incumbent} vs {cid}"
                        )
                    cross_domain_ambiguous[key] = sorted({incumbent, cid})
                    reviews[key].append((incumbent, automatic[key][1], surface, "review"))
                    reviews[key].append((cid, language, surface, "review"))
                    del automatic[key]
                    continue
                if key in automatic and automatic[key][1] != language:
                    automatic[key] = (cid, "shared", surface, source)
                else:
                    automatic[key] = value
    for record in by_id.values():
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

    # A hierarchy with a cycle is not a hierarchy. The inverse checks above are per EDGE and
    # cannot see one: A broader B, B broader C, C broader A satisfies every inverse and still
    # says each of the three is a kind of itself. The amendment op refuses the immediate
    # inversion (A->B when B->A exists) and is equally blind past one hop, so three separate,
    # individually valid amendments compose into a loop that loads clean. Confirmed by building
    # exactly that over technical.duration, technical.convexity and technical.irr: it loaded
    # without an error.
    #
    # Iterative DFS over a sorted adjacency, so the reported cycle is the same one on every run
    # rather than whichever order a set happened to yield.
    broader_of = {cid: sorted(record["relations"]["broader"]) for cid, record in by_id.items()}
    state: dict[str, int] = {}          # 0 = on the current path, 1 = finished
    for origin in sorted(broader_of):
        if state.get(origin) == 1:
            continue
        stack = [(origin, iter(broader_of[origin]))]
        path = [origin]
        state[origin] = 0
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if state.get(child) == 0:
                    loop = path[path.index(child):] + [child]
                    raise ContractError(
                        "broader/narrower relations form a cycle: " + " -> ".join(loop)
                    )
                if state.get(child) != 1:
                    state[child] = 0
                    path.append(child)
                    stack.append((child, iter(broader_of.get(child, ()))))
                    advanced = True
                    break
            if not advanced:
                state[node] = 1
                stack.pop()
                path.pop()

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
        # Surfaces two DISJOINT domains both claim automatically. Empty for every scoped load
        # by construction; non-empty only when `contexts=None` merges packs that were built to
        # be loaded apart. Reported rather than hidden: a caller seeing `premise` fail to fire
        # deserves to know it is because two packs own it, not because it is missing.
        "cross_domain_ambiguous": {
            surface: sorted(owners) for surface, owners in sorted(cross_domain_ambiguous.items())
        },
        "contexts": sorted(scope) if scope else None,
        "contexts_available": sorted({
            str(name).casefold()
            for record in by_id.values() for name in record.get("contexts", [])
        }),
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
