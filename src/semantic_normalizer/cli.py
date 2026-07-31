"""Command-line interface for semantic-normalizer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evaluator import evaluate_ablations
from .exporters import export_skos, export_synonym_graph
from .normalizer import (
    KNOWN_SUFFIXES,
    SCHEMA_VERSION,
    TOOL_VERSION,
    canonical_json,
    evaluate_golden,
    evaluate_retrieval,
    indexed_paths,
    infer_kind,
    normalize_text,
    read_utf8,
    write_jsonl,
)
from .reconciliation import apply_workspace_response, create_workspace_request
from .registry import ContractError, automatic_surfaces, init_workspace, load_registry


def _registry(args) -> dict:
    return load_registry(
        args.registry, args.registry_schema, contexts=getattr(args, "contexts", None)
    )


def _input(args) -> tuple[str, str, Path | None]:
    if getattr(args, "text", None) is not None:
        return args.text, "<query>", None
    if not getattr(args, "input", None):
        raise ContractError("provide INPUT or --text")
    path = Path(args.input)
    _, text = read_utf8(path)
    return text, str(path), path


def command_validate(args) -> int:
    registry = _registry(args)
    print(canonical_json({
        "valid": True,
        "records": len(registry["records"]),
        "registry_version": registry["version"],
        "registry_sha256": registry["hash"],
        "registry_schema_sha256": registry["schema_hash"],
        "automatic_keys": len(registry["automatic"]),
        "review_keys": len(registry["reviews"]),
    }))
    return 0


def command_normalize(args) -> int:
    registry = _registry(args)
    text, source, path = _input(args)
    records = normalize_text(text, source, infer_kind(path, args.kind), registry)
    write_jsonl(records, Path(args.output) if args.output else None)
    return 0


def command_query(args) -> int:
    registry = _registry(args)
    text, source, path = _input(args)
    records = normalize_text(text, source, infer_kind(path, args.kind), registry)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "registry_version": registry["version"],
        "registry_sha256": registry["hash"],
        "registry_schema_sha256": registry["schema_hash"],
        "original": text,
        "segments": records,
        "concept_ids": list(dict.fromkeys(
            cid for record in records for cid in record["concept_ids"]
        )),
        "concept_tokens": list(dict.fromkeys(
            token for record in records for token in record["concept_tokens"]
        )),
        "search_text": " ".join(
            record["text_expanded"] for record in records if record["text_expanded"]
        ),
        "needs_review": any(record["needs_review"] for record in records),
    }
    print(canonical_json(payload))
    return 0


def command_index(args) -> int:
    registry = _registry(args)
    root, output_dir = Path(args.input), Path(args.output_dir)
    records = []
    for path in indexed_paths(root, output_dir):
        _, text = read_utf8(path)
        records.extend(normalize_text(text, str(path), infer_kind(path, "auto"), registry))
    records.sort(key=lambda record: (record["source"], record["start"], record["end"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(records, output_dir / "index.jsonl")
    rg_lines = []
    for record in records:
        approved_surfaces = []
        for concept_id in record["concept_ids"]:
            item = registry["by_id"][concept_id]
            for language in ("en", "pt-BR"):
                approved_surfaces.extend(automatic_surfaces(item, language))
        values = [
            record["source"], str(record["line"]),
            record["original"].replace("\n", "\\n"),
            *approved_surfaces,
            *record["concept_ids"],
            *record["concept_tokens"],
        ]
        rg_lines.append("\t".join(dict.fromkeys(value for value in values if value)))
    (output_dir / "rg.txt").write_text(
        "\n".join(rg_lines) + ("\n" if rg_lines else ""), encoding="utf-8"
    )
    print(canonical_json({
        "documents": len({record["source"] for record in records}),
        "segments": len(records),
        "registry_sha256": registry["hash"],
        "index": str(output_dir / "index.jsonl"),
        "rg": str(output_dir / "rg.txt"),
    }))
    return 0


def command_evaluate(args) -> int:
    registry = _registry(args)
    fixture = Path(args.fixture)
    _, text = read_utf8(fixture)
    try:
        dataset = json.loads(text) if text.lstrip().startswith("{") else None
    except json.JSONDecodeError:
        dataset = None
    if isinstance(dataset, dict) and "documents" in dataset and "queries" in dataset:
        report = evaluate_ablations(dataset, registry)
    else:
        report = evaluate_golden(fixture, registry)
    content = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)
    return 1 if report.get("failed", 0) else 0


def command_reconcile_request(args) -> int:
    request = create_workspace_request(
        args.workspace, args.context, args.start, args.end, args.language
    )
    print(canonical_json(request))
    return 0


def command_reconcile_apply(args) -> int:
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    response = json.loads(Path(args.response).read_text(encoding="utf-8"))
    print(canonical_json(apply_workspace_response(
        args.workspace,
        request,
        response,
        args.reviewer,
        args.rationale,
        args.protected_slot_comparison,
        args.timestamp,
    )))
    return 0


def command_export(args) -> int:
    registry = _registry(args)
    content = (
        export_skos(registry, args.base_iri)
        if args.format == "skos"
        else export_synonym_graph(registry)
    )
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)
    return 0


def command_init_workspace(args) -> int:
    print(canonical_json(
        init_workspace(args.directory, args.registry, args.registry_schema)
    ))
    return 0


def _registry_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--registry")
    command.add_argument("--registry-schema")
    command.add_argument("--lexicon", dest="registry", help=argparse.SUPPRESS)
    # Domain scoping was reachable from `load_lexicon(contexts=...)` and NOT from the CLI, which
    # made the plug-and-play packaging a Python-only feature — the same shape of gap as a
    # declarative field no consumer reads. Anyone driving this from a shell could only ever load
    # every domain at once, which is the merged table the scoping exists to avoid.
    command.add_argument(
        "--contexts", nargs="+", metavar="DOMAIN",
        help="load only these domain packs, e.g. --contexts core cga. "
             "Omit to load every concept in the registry.",
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="semantic-normalizer", description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-registry")
    _registry_options(validate)
    validate.set_defaults(func=command_validate)
    for name, function in (("normalize", command_normalize), ("query", command_query)):
        command = sub.add_parser(name)
        command.add_argument("input", nargs="?")
        command.add_argument("--text")
        command.add_argument("--kind", choices=("auto", "markdown", "text", "python"), default="auto")
        command.add_argument("--output") if name == "normalize" else None
        _registry_options(command)
        command.set_defaults(func=function)
    index = sub.add_parser("index")
    index.add_argument("input")
    index.add_argument("--output-dir", required=True)
    _registry_options(index)
    index.set_defaults(func=command_index)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("fixture")
    evaluate.add_argument("--output")
    _registry_options(evaluate)
    evaluate.set_defaults(func=command_evaluate)
    request = sub.add_parser("reconcile-request")
    request.add_argument("--workspace", required=True)
    request.add_argument("--context", required=True)
    request.add_argument("--start", type=int, required=True)
    request.add_argument("--end", type=int, required=True)
    request.add_argument("--language", choices=("en", "pt-BR"), required=True)
    request.set_defaults(func=command_reconcile_request)
    apply = sub.add_parser("reconcile-apply")
    apply.add_argument("--workspace", required=True)
    apply.add_argument("--request", required=True)
    apply.add_argument("--response", required=True)
    apply.add_argument("--reviewer", required=True)
    apply.add_argument("--rationale", required=True)
    apply.add_argument(
        "--protected-slot-comparison",
        choices=("preserved", "changed", "not-applicable"),
        required=True,
    )
    apply.add_argument("--timestamp")
    apply.set_defaults(func=command_reconcile_apply)
    export = sub.add_parser("export")
    export.add_argument("format", choices=("skos", "synonym-graph"))
    export.add_argument("--base-iri", default="https://cga-game.local/concept")
    export.add_argument("--output")
    _registry_options(export)
    export.set_defaults(func=command_export)
    workspace = sub.add_parser("init-workspace")
    workspace.add_argument("directory")
    _registry_options(workspace)
    workspace.set_defaults(func=command_init_workspace)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        return args.func(args)
    except (ContractError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
