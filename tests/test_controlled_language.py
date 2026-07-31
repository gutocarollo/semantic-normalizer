from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "controlled_language.py"
REGISTRY = ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl"
REGISTRY_SCHEMA = ROOT / "src" / "semantic_normalizer" / "data" / "registry.schema.json"
GOLDEN = ROOT / "tests" / "fixtures" / "golden.jsonl"
DEV = ROOT / "tests" / "fixtures" / "dev_retrieval.json"

SPEC = importlib.util.spec_from_file_location("controlled_language", SCRIPT)
assert SPEC and SPEC.loader
cl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cl)


class ControlledLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lexicon = cl.load_registry(REGISTRY, REGISTRY_SCHEMA)

    def normalize(self, text, kind="text"):
        return cl.normalize_text(text, "<test>", kind, self.lexicon)

    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            text=True, capture_output=True, check=False,
        )

    def test_01_registry_is_valid_and_hashed(self):
        self.assertEqual(257, len(self.lexicon["records"]))
        self.assertEqual(64, len(self.lexicon["hash"]))
        self.assertEqual("2.6.0", self.lexicon["version"])

    def test_02_validate_registry_cli(self):
        result = self.cli(
            "validate-registry", "--registry", REGISTRY,
            "--registry-schema", REGISTRY_SCHEMA,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(self.lexicon["hash"], payload["registry_sha256"])

    def test_03_golden_has_at_least_36_cases(self):
        cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(cases), 36)
        self.assertTrue({"text", "markdown", "python"}.issubset(
            {case["kind"] for case in cases if "kind" in case}
        ))
        self.assertTrue({"invalid_suffix", "invalid_utf8", "query", "index", "retrieval"}.issubset(
            {case["type"] for case in cases}
        ))

    def test_04_golden_evaluator_runs_real_normalizer(self):
        report = cl.evaluate_golden(GOLDEN, self.lexicon)
        self.assertEqual(report["cases"], report["passed"], report)
        self.assertEqual([], report["failures"])

    def test_05_nfc_casefold_is_primary_and_ascii_is_fallback(self):
        decomposed = "O usua\u0301rio preserva o reposito\u0301rio."
        record = self.normalize(decomposed)[0]
        self.assertIn("actor.user", record["concept_ids"])
        self.assertIn("entity.repository", record["concept_ids"])
        self.assertIn("usuario", record["search_fields"]["ascii_fallback"])
        self.assertIn("usua\u0301rio", record["original"])

    def test_06_longest_match_consumes_end_user_once(self):
        record = self.normalize("The end user checks the file.")[0]
        matches = [event for event in record["match_events"] if event["concept_id"] == "actor.user"]
        self.assertEqual(1, len(matches))
        self.assertEqual("end user", matches[0]["alias"])

    def test_07_review_alias_lists_all_candidates_without_concept(self):
        record = self.normalize("Check the base.")[0]
        candidates = {item["concept_id"] for item in record["ambiguous_candidates"][0]["candidates"]}
        self.assertEqual({"entity.facility_base", "technical.numeral_base"}, candidates)
        self.assertNotIn("entity.facility_base", record["concept_ids"])
        self.assertNotIn("technical.numeral_base", record["concept_ids"])
        self.assertTrue(record["needs_review"])

    def test_08_markdown_protection_excludes_code_url_destination_and_placeholder(self):
        text = "Preserve `delete file`, [file](docs/delete.md), https://x.test/delete and ${{ delete_file }}."
        record = self.normalize(text, "markdown")[0]
        self.assertIn("action.preserve", record["concept_ids"])
        self.assertNotIn("action.delete", record["concept_ids"])
        kinds = {span["kind"] for span in record["protected"]}
        self.assertTrue({"inline_code", "link_destination", "url", "placeholder"}.issubset(kinds))

    def test_09_markdown_frontmatter_and_fence_are_protected(self):
        text = "---\ntitle: Delete file\n---\nValidate file.\n```python\ndelete(file)\n```\n"
        records = self.normalize(text, "markdown")
        all_protected = [span["kind"] for record in records for span in record["protected"]]
        concepts = [cid for record in records for cid in record["concept_ids"]]
        self.assertIn("frontmatter", all_protected)
        self.assertIn("fenced_code", all_protected)
        self.assertNotIn("action.delete", concepts)
        self.assertIn("action.validate", concepts)

    def test_09a_html_code_block_is_protected_in_markdown(self):
        text = 'Preserve <code class="sample">delete file</code> and validate the document.'
        record = self.normalize(text, "markdown")[0]
        protected = next(
            item for item in record["protected"]
            if item["kind"] == "html_code_block"
        )
        self.assertEqual(
            '<code class="sample">delete file</code>',
            protected["original"],
        )
        self.assertEqual(
            protected["original"],
            text[protected["start"]:protected["end"]],
        )
        self.assertEqual(text, record["original"])
        self.assertIn(protected["original"], record["canonical_text"])
        self.assertFalse(any(
            protected["start"] <= event["start"] < protected["end"]
            for event in record["match_events"]
        ))
        self.assertNotIn("action.delete", record["concept_ids"])
        self.assertNotIn("entity.file", record["concept_ids"])

    def test_09b_html_pre_block_is_protected_in_text(self):
        text = "Retain <pre>INDEX create delete file</pre> and validate the document."
        record = self.normalize(text, "text")[0]
        protected = next(
            item for item in record["protected"]
            if item["kind"] == "html_pre_block"
        )
        self.assertEqual(
            "<pre>INDEX create delete file</pre>",
            protected["original"],
        )
        self.assertEqual(
            protected["original"],
            text[protected["start"]:protected["end"]],
        )
        self.assertEqual(text, record["original"])
        self.assertIn(protected["original"], record["canonical_text"])
        self.assertFalse(any(
            protected["start"] <= event["start"] < protected["end"]
            for event in record["match_events"]
        ))
        self.assertNotIn("action.index", record["concept_ids"])
        self.assertNotIn("action.create", record["concept_ids"])
        self.assertNotIn("action.delete", record["concept_ids"])
        self.assertNotIn("entity.file", record["concept_ids"])

    def test_10_python_extracts_only_comments_and_docstrings(self):
        text = (
            'ordinary = "delete file"\n'
            '# Preserve the file\n'
            'def f():\n'
            '    """Validate the document."""\n'
            '    value = "remove document"\n'
            '    return value\n'
        )
        records = self.normalize(text, "python")
        self.assertEqual(["python_comment", "python_docstring"], [record["segment_kind"] for record in records])
        concepts = [cid for record in records for cid in record["concept_ids"]]
        self.assertIn("action.preserve", concepts)
        self.assertIn("action.validate", concepts)
        self.assertNotIn("action.delete", concepts)
        self.assertNotIn("action.remove", concepts)

    def test_11_python_offsets_and_utf8_offsets_reference_source(self):
        text = 'x = "á"\n# O usuário valida o arquivo\n'
        record = self.normalize(text, "python")[0]
        self.assertEqual(record["original"], text[record["start"]:record["end"]])
        encoded = text.encode("utf-8")
        self.assertEqual(record["original"], encoded[record["byte_start"]:record["byte_end"]].decode("utf-8"))
        self.assertEqual(2, record["line"])

    def test_12_raw_never_becomes_semantic_unit(self):
        record = self.normalize("Unmapped quaternion preserves payload.")[0]
        self.assertTrue(record["unresolved"])
        self.assertTrue(all(unit["governance"] == "approved_lexicon" for unit in record["semantic_units"]))
        self.assertTrue(all("concept_id" not in item for item in record["semantic_sequence"] if item["type"] == "raw"))

    def test_13_simple_active_actor_object_relations(self):
        record = self.normalize("The user preserve the file.")[0]
        types = {relation["type"] for relation in record["semantic_relations"]}
        roles = {unit["role"] for unit in record["semantic_units"]}
        self.assertTrue({"actor_of", "object_of"}.issubset(types))
        self.assertTrue({"actor", "action", "object"}.issubset(roles))

    def test_14_negation_and_condition_relations_have_evidence(self):
        record = self.normalize("If the job fails, do not delete the file.")[0]
        types = {relation["type"] for relation in record["semantic_relations"]}
        self.assertTrue({"condition_of", "negates"}.issubset(types))
        for relation in record["semantic_relations"]:
            self.assertLess(relation["evidence"]["start"], relation["evidence"]["end"])

    def test_15_temporal_order_encodes_both_directions(self):
        before = self.normalize("Validate the file before delete the document.")[0]
        after = self.normalize("Delete the file after validate the document.")[0]
        self.assertEqual({"before", "after"}, {r["type"] for r in before["semantic_relations"]})
        self.assertEqual({"before", "after"}, {r["type"] for r in after["semantic_relations"]})

    def test_16_coordination_and_multiple_actions_abstain(self):
        record = self.normalize("Validate and delete the file.")[0]
        self.assertTrue(record["needs_review"])
        self.assertIn("finite_grammar_abstained_coordination_or_anaphora", record["warnings"])
        self.assertEqual([], record["semantic_relations"])

    def test_17_output_is_byte_deterministic(self):
        first = [cl.canonical_json(record) for record in self.normalize("O usuário final mantém intacto o arquivo.")]
        second = [cl.canonical_json(record) for record in self.normalize("O usuário final mantém intacto o arquivo.")]
        self.assertEqual(first, second)

    def test_18_normalized_search_is_concept_idempotent(self):
        first = self.normalize("Retain the file.")[0]
        second = self.normalize(cl.normalized_search(first))[0]
        self.assertEqual(set(first["concept_ids"]), set(second["concept_ids"]))

    def test_19_unknown_suffix_fails_closed_unless_text_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xyz"
            path.write_text("Validate the file.", encoding="utf-8")
            failed = self.cli("normalize", path, "--registry", REGISTRY)
            accepted = self.cli("normalize", path, "--kind", "text", "--registry", REGISTRY)
        self.assertEqual(2, failed.returncode)
        self.assertIn("unknown suffix", failed.stderr)
        self.assertEqual(0, accepted.returncode, accepted.stderr)

    def test_20_invalid_utf8_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.txt"
            path.write_bytes(b"\xff\xfe")
            result = self.cli("normalize", path, "--registry", REGISTRY)
        self.assertEqual(2, result.returncode)
        self.assertIn("not valid UTF-8", result.stderr)

    def test_21_automatic_collision_is_rejected(self):
        lines = REGISTRY.read_text(encoding="utf-8").splitlines()
        # Collide with whatever the first record's preferred English label happens to be,
        # instead of hardcoding one. The literal used to be "approve", which stopped being
        # the first record the moment the registry grew and re-sorted.
        victim = json.loads(lines[0])["labels"]["en"]["pref"]
        colliding = json.loads(lines[1])
        colliding["concept_id"] = "action.collision"
        colliding["labels"]["en"]["pref"] = victim
        colliding["lexical_forms"]["en"][0]["form"] = victim
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text(lines[0] + "\n" + json.dumps(colliding, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(cl.ContractError):
                cl.load_registry(path, REGISTRY_SCHEMA)

    def test_22_query_uses_same_lexicon_hash_and_pipeline(self):
        result = self.cli("query", "--text", "consulta de busca", "--kind", "text", "--registry", REGISTRY)
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(self.lexicon["hash"], payload["registry_sha256"])
        self.assertIn("entity.query", payload["concept_ids"])

    def test_22a_pref_alt_and_hidden_labels_can_require_review(self):
        records = [
            json.loads(line)
            for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        ]
        target = next(
            record for record in records if record["concept_id"] == "action.approve"
        )
        target["labels"]["en"]["hidden"] = ["authorize"]
        target["lexical_forms"]["en"].append({
            "form": "authorize",
            "features": {},
            "policy": "review",
        })
        target["lexical_forms"]["en"][0]["policy"] = "review"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.jsonl"
            path.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )
            loaded = cl.load_registry(path, REGISTRY_SCHEMA)
        self.assertNotIn("approve", loaded["automatic"])
        self.assertIn("approve", loaded["reviews"])
        self.assertIn("authorize", loaded["reviews"])

    def test_22b_observed_label_cannot_be_auto_or_lexically_duplicated(self):
        records = [
            json.loads(line)
            for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        ]
        target = next(
            record for record in records if record["concept_id"] == "action.approve"
        )
        target["labels"]["en"]["observed"] = ["authorize"]
        target["lexical_forms"]["en"].append({
            "form": "authorize",
            "features": {},
            "policy": "auto",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.jsonl"
            path.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                cl.ContractError, "observed labels must use review policy"
            ):
                cl.load_registry(path, REGISTRY_SCHEMA)
        target["lexical_forms"]["en"][-1]["policy"] = "review"
        target["lexical_forms"]["en"].append({
            "form": "AUTHORIZE",
            "features": {},
            "policy": "auto",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.jsonl"
            path.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(cl.ContractError, "duplicate lexical form"):
                cl.load_registry(path, REGISTRY_SCHEMA)

    def test_23_index_emits_raw_aliases_concepts_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            first, second = Path(directory) / "out1", Path(directory) / "out2"
            root.mkdir()
            (root / "a.md").write_text("Retain the file.", encoding="utf-8")
            (root / "b.py").write_text('# Preserve the document\nx = "delete file"\n', encoding="utf-8")
            one = self.cli("index", root, "--output-dir", first, "--registry", REGISTRY)
            two = self.cli("index", root, "--output-dir", second, "--registry", REGISTRY)
            self.assertEqual(0, one.returncode, one.stderr)
            self.assertEqual(0, two.returncode, two.stderr)
            self.assertEqual((first / "index.jsonl").read_bytes(), (second / "index.jsonl").read_bytes())
            self.assertEqual((first / "rg.txt").read_bytes(), (second / "rg.txt").read_bytes())
            rg = (first / "rg.txt").read_text(encoding="utf-8")
        self.assertIn("Retain the file.", rg)
        self.assertIn("retain", rg)
        self.assertIn("action.preserve", rg)

    def test_24_bm25_tie_break_is_deterministic(self):
        documents = [("b", "same token"), ("a", "same token")]
        self.assertEqual(["a", "b"], [doc for doc, _ in cl.bm25_rank(documents, "token")])

    def test_25_dev_evaluation_has_all_required_metrics_and_ci(self):
        dataset = json.loads(DEV.read_text(encoding="utf-8"))
        report = cl.evaluate_retrieval(dataset, self.lexicon)
        self.assertEqual(16, report["queries"])
        self.assertEqual(8, report["documents"])
        for name in ("recall@5", "precision@5", "mrr@10", "ndcg@10"):
            self.assertIn(name, report["metrics"])
            self.assertEqual(2, len(report["metrics"][name]["paired_bootstrap_ci95"]))
        self.assertIn("exact_literal_regressions", report)
        self.assertIn("concept_coverage", report)
        self.assertIn("review_abstention", report)

    def test_26_evaluate_cli_supports_golden_jsonl_and_dev_shape(self):
        golden = self.cli("evaluate", GOLDEN, "--registry", REGISTRY)
        dev = self.cli("evaluate", DEV, "--registry", REGISTRY)
        self.assertEqual(0, golden.returncode, golden.stderr)
        self.assertEqual(0, dev.returncode, dev.stderr)
        self.assertEqual("golden", json.loads(golden.stdout)["evaluation_type"])
        self.assertEqual("retrieval", json.loads(dev.stdout)["evaluation_type"])

    def test_27_normalize_never_changes_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.md"
            output = Path(directory) / "sidecar.jsonl"
            original = b"Preserve `delete file`.\n"
            source.write_bytes(original)
            result = self.cli("normalize", source, "--output", output, "--registry", REGISTRY)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(original, source.read_bytes())
            self.assertTrue(output.read_text(encoding="utf-8"))

    def test_28_default_package_loader_and_explicit_overrides_match(self):
        packaged = cl.load_registry()
        explicit = cl.load_registry(REGISTRY, REGISTRY_SCHEMA)
        self.assertEqual(explicit["hash"], packaged["hash"])
        self.assertEqual(explicit["schema_hash"], packaged["schema_hash"])
        self.assertEqual(257, len(packaged["records"]))

    def test_29_invalid_schema_override_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "schema.json"
            schema.write_text("{}", encoding="utf-8")
            with self.assertRaises(cl.ContractError):
                cl.load_registry(REGISTRY, schema)

    def test_30_canonical_rewrite_is_auto_only_and_reversible(self):
        record = self.normalize("Retain the file.")[0]
        self.assertEqual("preserve the file.", record["canonical_text"])
        self.assertEqual("accepted", record["canonical_status"])
        mapping = record["canonical_mappings"][0]
        self.assertEqual(
            mapping["original"],
            record["original"][mapping["original_start"]:mapping["original_end"]],
        )
        self.assertEqual(
            mapping["canonical"],
            record["canonical_text"][mapping["canonical_start"]:mapping["canonical_end"]],
        )
        self.assertEqual("auto", mapping["policy"])

    def test_31_plan_example_resolves_and_protects_app_identifier(self):
        """The example the design plan is built on, now that registry 2.1.0 covers it.

        Until 2.0.0 the registry had no `action.start`, so this sentence could only abstain.
        The 0.2.0 import added it with `iniciar` preferred and `começar` as an alternative,
        which is exactly the mapping SKILL.md documents. The invariants that mattered before
        still hold: the source is untouched and APP-01 stays protected at the same offsets.
        """
        text = "O operador deve começar o servidor APP-01."
        record = self.normalize(text)[0]
        self.assertEqual(text, record["original_text"])
        self.assertEqual(
            "O operador deve iniciar o servidor APP-01.", record["canonical_text"]
        )
        self.assertIn("action.start", record["concept_ids"])
        self.assertIn("actor.operator", record["concept_ids"])
        self.assertIn("system.server", record["concept_ids"])
        # Still `review`: resolving three concepts does not certify the whole sentence.
        self.assertEqual("review", record["canonical_status"])
        self.assertIn(
            {"value": "APP-01", "start": 35, "end": 41},
            record["protected_values"],
        )
        self.assertNotIn("APP-01", record["canonical_text"][:35])
        self.assertTrue(record["reconciliation"])

    def test_32_governed_sha256_wins_over_generic_identifier_protection(self):
        record = self.normalize("Use SHA-256 with APP-01.")[0]
        self.assertIn("technical.sha256", record["concept_ids"])
        self.assertNotIn("SHA-256", [item["value"] for item in record["protected_values"]])
        self.assertIn("APP-01", [item["value"] for item in record["protected_values"]])

    def test_33_v2_projection_tokens_and_registry_hashes(self):
        record = self.normalize("If the job fails, do not delete the file.")[0]
        self.assertEqual(self.lexicon["hash"], record["registry_sha256"])
        self.assertEqual(self.lexicon["schema_hash"], record["registry_schema_sha256"])
        self.assertIn("c__condition__if", record["concept_tokens"])
        self.assertIn("condition__if", record["operator_tokens"])
        self.assertIn("c__action__delete", record["text_expanded"])

    def test_34_reconciliation_accepts_only_allow_list_or_abstain(self):
        request = cl.make_request(
            "Check the base.", 10, 14, "en",
            ["entity.facility_base", "technical.numeral_base"], 1, self.lexicon,
        )
        accepted = cl.apply_response(request, {
            "candidate_id": "technical.numeral_base",
            "confidence": 0.9,
            "evidence_span": {"start": 10, "end": 14, "text": "base"},
            "reason_code": "CONTEXT_DISAMBIGUATED",
        })
        self.assertEqual("accepted", accepted.state)
        self.assertEqual(
            ("candidate", "unresolved", "constrained_resolution", "validation", "accepted"),
            accepted.transition_trace,
        )
        with self.assertRaises(cl.ContractError):
            cl.apply_response(request, {
                "candidate_id": "entity.file",
                "confidence": 0.9,
                "evidence_span": {"start": 10, "end": 14, "text": "base"},
                "reason_code": "CONTEXT_DISAMBIGUATED",
            })

    def test_35_reconciliation_rejects_attempt_span_confidence_and_reason_attacks(self):
        with self.assertRaises(cl.ContractError):
            cl.make_request(
                "base", 0, 4, "en",
                ["entity.facility_base", "technical.numeral_base"], 3, self.lexicon,
            )
        request = cl.make_request(
            "base", 0, 4, "en",
            ["entity.facility_base", "technical.numeral_base"], 2, self.lexicon,
        )
        base = {
            "candidate_id": "ABSTAIN",
            "confidence": 0.0,
            "evidence_span": {"start": 0, "end": 4, "text": "base"},
            "reason_code": "INSUFFICIENT_CONTEXT",
        }
        result = cl.apply_response(request, base)
        self.assertEqual("review", result.state)
        for mutation in (
            {"confidence": 1.1},
            {"reason_code": "INVENTED"},
            {"evidence_span": {"start": 0, "end": 3, "text": "base"}},
            {"reason_code": "EXACT_APPROVED_FORM"},
        ):
            response = dict(base)
            response.update(mutation)
            with self.assertRaises(cl.ContractError):
                cl.apply_response(request, response)

    def test_36_reconciliation_cli_request_and_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            cl.init_workspace(workspace)
            request_result = self.cli(
                "reconcile-request", "--workspace", workspace,
                "--context", "base", "--start", 0, "--end", 4,
                "--language", "en",
            )
            self.assertEqual(0, request_result.returncode, request_result.stderr)
            request = Path(directory) / "request.json"
            response = Path(directory) / "response.json"
            request.write_text(request_result.stdout, encoding="utf-8")
            response.write_text(json.dumps({
                "candidate_id": "ABSTAIN",
                "confidence": 0,
                "evidence_span": {"start": 0, "end": 4, "text": "base"},
                "reason_code": "INSUFFICIENT_CONTEXT",
            }), encoding="utf-8")
            applied = self.cli(
                "reconcile-apply", "--workspace", workspace,
                "--request", request, "--response", response,
                "--reviewer", "test-reviewer", "--rationale", "context is insufficient",
                "--protected-slot-comparison", "not-applicable",
            )
        self.assertEqual(0, applied.returncode, applied.stderr)
        self.assertEqual("unresolved", json.loads(applied.stdout)["result"]["state"])

    def test_37_exports_are_deterministic_and_exclude_review_taxonomy(self):
        first = cl.export_skos(self.lexicon)
        second = cl.export_skos(self.lexicon)
        self.assertEqual(first, second)
        self.assertIn("skos/core#prefLabel", first)
        self.assertNotIn('"base"@en', first)
        synonyms = cl.export_synonym_graph(self.lexicon)
        self.assertNotIn("base", [part.strip() for line in synonyms.splitlines() for part in line.split(",")])
        self.assertNotIn("must", [part.strip() for line in synonyms.splitlines() for part in line.split(",")])

    def test_38_init_workspace_copies_seed_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            result = cl.init_workspace(workspace)
            self.assertEqual(
                ["candidates.jsonl", "decisions.jsonl", "registry.jsonl", "registry.schema.json"],
                result["files"],
            )
            self.assertEqual(REGISTRY.read_bytes(), (workspace / "registry.jsonl").read_bytes())
            self.assertEqual(b"", (workspace / "candidates.jsonl").read_bytes())
            with self.assertRaises(cl.ContractError):
                cl.init_workspace(workspace)

    def test_39_cli_defaults_to_package_registry(self):
        result = self.cli("query", "--text", "Retain the file.", "--kind", "text")
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(self.lexicon["hash"], payload["registry_sha256"])
        self.assertIn("action.preserve", payload["concept_ids"])

    def test_40_v2_output_is_deterministic(self):
        text = "Use SHA-256 with APP-01."
        first = [cl.canonical_json(record) for record in self.normalize(text)]
        second = [cl.canonical_json(record) for record in self.normalize(text)]
        self.assertEqual(first, second)

    def test_41_default_loader_reads_package_data_from_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "semantic-normalizer.zip"
            source = ROOT / "src"
            with zipfile.ZipFile(archive, "w") as bundle:
                for path in sorted((source / "semantic_normalizer").rglob("*")):
                    if path.is_file() and "__pycache__" not in path.parts:
                        bundle.write(path, path.relative_to(source))
            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    (
                        "from semantic_normalizer import load_registry;"
                        "r=load_registry();"
                        "print(len(r['records']),r['version'],len(r['hash']))"
                    ),
                ],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(archive)},
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("257 2.6.0 64", result.stdout.strip())

    def test_42_canonical_mapping_has_local_and_absolute_offsets(self):
        records = self.normalize("First line.\nRetain the file.")
        record = records[1]
        mapping = record["canonical_mappings"][0]
        self.assertEqual("Retain", record["original"][mapping["original_start"]:mapping["original_end"]])
        source = "First line.\nRetain the file."
        self.assertEqual("Retain", source[mapping["source_start"]:mapping["source_end"]])

    def test_43_runtime_review_aliases_follow_policy_including_preferred_forms(self):
        self.assertIn("create", self.lexicon["by_id"]["action.create"]["review_aliases"]["en"])
        self.assertIn("index", self.lexicon["by_id"]["action.index"]["review_aliases"]["en"])
        self.assertIn("se", self.lexicon["by_id"]["condition.if"]["review_aliases"]["pt-BR"])
        self.assertNotIn("create", self.lexicon["by_id"]["action.create"]["auto_aliases"]["en"])


if __name__ == "__main__":
    unittest.main()
