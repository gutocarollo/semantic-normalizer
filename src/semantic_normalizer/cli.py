from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .evaluation import evaluate_retrieval
from .loop import NormalizationLoop, StaticResolver
from .normalizer import SemanticNormalizer
from .registry import ConceptRegistry


def default_registry_path() -> Path:
    project_path = Path(__file__).resolve().parents[2] / "config" / "concepts.json"
    if project_path.exists():
        return project_path
    package_path = Path(__file__).resolve().parent / "data" / "concepts.json"
    if package_path.exists():
        return package_path
    raise FileNotFoundError("Cannot locate the default concept registry")


def _add_registry_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--registry",
        default=str(default_registry_path()),
        help="Path to the concept registry JSON file",
    )


def _write_json(payload: Any, *, output: str | None, pretty: bool = True) -> None:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=False,
    )
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _load_text(text: str | None, input_path: str | None) -> str:
    if text is not None and input_path is not None:
        raise ValueError("Use --text or --input, not both")
    if text is not None:
        return text
    if input_path is not None:
        return Path(input_path).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("Provide --text, --input, or stdin")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="semantic-normalizer",
        description=(
            "Create a reversible English-Portuguese canonical projection for lexical retrieval."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    normalize_parser = subparsers.add_parser("normalize", help="Normalize one text")
    _add_registry_argument(normalize_parser)
    normalize_parser.add_argument("--text")
    normalize_parser.add_argument("--input")
    normalize_parser.add_argument("--output")
    normalize_parser.add_argument("--lang", default="auto", choices=("auto", "en", "pt"))
    normalize_parser.add_argument("--target-lang", default="source", choices=("source", "en", "pt"))
    normalize_parser.add_argument("--pretty", action="store_true")
    normalize_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return exit code 2 unless status is accepted",
    )
    resolver_group = normalize_parser.add_mutually_exclusive_group()
    resolver_group.add_argument(
        "--decision-file",
        help='JSON object mapping lower-cased surface forms to approved concept IDs',
    )
    resolver_group.add_argument(
        "--agent-model",
        help='Instructor provider/model string, for example "ollama/qwen3:8b"',
    )
    normalize_parser.add_argument("--max-attempts", type=int, default=2)

    evaluate_parser = subparsers.add_parser("evaluate", help="Compare raw and normalized BM25")
    _add_registry_argument(evaluate_parser)
    evaluate_parser.add_argument("--documents", required=True)
    evaluate_parser.add_argument("--queries", required=True)
    evaluate_parser.add_argument("--output")
    evaluate_parser.add_argument("--target-lang", default="en", choices=("en", "pt"))
    evaluate_parser.add_argument("--k", nargs="+", type=int, default=[1, 3, 5])
    evaluate_parser.add_argument("--summary-only", action="store_true")

    validate_parser = subparsers.add_parser(
        "validate-registry",
        help="Validate concept IDs, labels, relations, and ambiguous aliases",
    )
    _add_registry_argument(validate_parser)
    validate_parser.add_argument("--output")

    skos_parser = subparsers.add_parser("export-skos", help="Export the registry as SKOS Turtle")
    _add_registry_argument(skos_parser)
    skos_parser.add_argument("--base-uri", default="https://example.org/semantic-normalizer/")
    skos_parser.add_argument("--output", required=True)

    synonym_parser = subparsers.add_parser(
        "export-synonyms",
        help="Export explicit alias-to-concept rules for Elasticsearch synonym_graph",
    )
    _add_registry_argument(synonym_parser)
    synonym_parser.add_argument("--output", required=True)

    inspect_parser = subparsers.add_parser("inspect-concept", help="Show one concept record")
    _add_registry_argument(inspect_parser)
    inspect_parser.add_argument("concept_id")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        registry = ConceptRegistry.from_path(args.registry)

        if args.command == "normalize":
            text = _load_text(args.text, args.input)
            normalizer = SemanticNormalizer(registry)
            resolver = None
            if args.decision_file:
                decisions = json.loads(Path(args.decision_file).read_text(encoding="utf-8"))
                if not isinstance(decisions, dict):
                    raise ValueError("--decision-file must contain a JSON object")
                resolver = StaticResolver({str(key): str(value) for key, value in decisions.items()})
            elif args.agent_model:
                from .adapters.instructor_resolver import InstructorResolver

                resolver = InstructorResolver(args.agent_model)
            result = NormalizationLoop(
                normalizer,
                resolver,
                max_attempts=args.max_attempts,
            ).run(
                text,
                source_language=args.lang,
                target_language=args.target_lang,
            )
            _write_json(result.to_dict(), output=args.output, pretty=args.pretty)
            if args.strict and result.status.value != "accepted":
                return 2
            return 0

        if args.command == "evaluate":
            report = evaluate_retrieval(
                normalizer=SemanticNormalizer(registry),
                documents_path=args.documents,
                queries_path=args.queries,
                k_values=tuple(sorted(set(args.k))),
                target_language=args.target_lang,
            )
            if args.summary_only:
                for mode in report["modes"].values():
                    mode.pop("per_query", None)
            _write_json(report, output=args.output, pretty=True)
            return 0

        if args.command == "validate-registry":
            diagnostics = [item.to_dict() for item in registry.validate()]
            payload = {
                "scheme_id": registry.scheme_id,
                "version": registry.version,
                "concept_count": len(registry.concepts),
                "diagnostics": diagnostics,
            }
            _write_json(payload, output=args.output, pretty=True)
            return 1 if any(item["severity"] == "error" for item in diagnostics) else 0

        if args.command == "export-skos":
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(registry.to_skos_turtle(args.base_uri), encoding="utf-8")
            print(output)
            return 0

        if args.command == "export-synonyms":
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(registry.to_elasticsearch_synonyms(), encoding="utf-8")
            print(output)
            return 0

        if args.command == "inspect-concept":
            concept = registry.get(args.concept_id)
            payload = {
                "concept_id": concept.concept_id,
                "preferred_labels": concept.preferred_labels,
                "alternative_labels": {
                    key: list(value) for key, value in concept.alternative_labels.items()
                },
                "hidden_labels": {
                    key: list(value) for key, value in concept.hidden_labels.items()
                },
                "surface_forms": {
                    key: list(value) for key, value in concept.surface_forms.items()
                },
                "definitions": concept.definitions,
                "part_of_speech": concept.part_of_speech,
                "domains": list(concept.domains),
                "source_authority": concept.source_authority,
                "status": concept.status,
            }
            _write_json(payload, output=None, pretty=True)
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 2
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
