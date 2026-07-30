# References and Prior Art

Reviewed on 2026-07-30. This catalog distinguishes direct prior art from adjacent components. No single project found implements the complete target: reversible English-Portuguese concept canonicalization, deterministic semantic guards, BM25 projections, and a bounded LLM disambiguation loop.

## 1. Closest conceptual foundations

| Reference | What it provides | Relationship to this project | Recommendation |
|---|---|---|---|
| ASD-STE100 Issue 9 — https://www.asd-ste100.org/about_STE.html | Controlled technical English: writing rules, controlled vocabulary, approved meanings and parts of speech, technical nouns and verbs. | Supports reduced lexical variation, stable terminology, and explicit construction. It does not specify bilingual concept IDs, BM25, or hallucination reduction. | Use as a writing-control influence, not as the canonicalization data model. |
| ASD guidance on tools — https://www.asd-ste100.org/STEsoftware.html | Limits of automatic checkers; need for a company glossary or termbase and human control. | Directly supports a review gate and rejects blind automatic conversion. | Keep the source immutable and require governed terminology approval. |
| W3C SKOS Primer — https://www.w3.org/TR/skos-primer/ | Concepts, one preferred label per language, alternative labels, hidden labels, broader/related mappings. | Best minimal model for language-neutral concept identity plus English and Portuguese labels. | Use SKOS as the core interchange and publication model. |
| W3C SKOS Reference — https://www.w3.org/TR/skos-reference/ | Normative SKOS vocabulary and integrity conditions. Hidden labels are intended for text indexing. | Fits preferred labels, synonyms, misspellings, search-only labels, and stable concept URIs. | Validate exports against SKOS integrity constraints. |
| OntoLex-Lemon — https://www.w3.org/community/ontolex/wiki/Final_Model_Specification | Rich morphology, syntax-semantics interface, decomposition, lexical variation, and translation. | Necessary when explicit surface-form lists become insufficient. | Add only after the SKOS registry and evaluation set stabilize. |
| TBX / ISO 30042 — https://www.tbxinfo.net/ | Standard exchange format for concept-oriented terminology databases. | Useful for interchange with translation and terminology-management systems. | Export/import TBX; do not make XML the internal runtime format. |
| W3C/OpenRefine Reconciliation API — https://openrefine.org/docs/technical-reference/reconciliation-api | Standard API for turning ambiguous labels plus context into ranked entity candidates with strong IDs. | Nearly identical to the candidate-generation and review boundary needed here. | Implement a reconciliation-compatible endpoint in a later service release. |

## 2. Direct or near-direct open-source projects

| Project | Scope | Useful components | Gap against the target |
|---|---|---|---|
| text2term — https://github.com/rsgoncalves/text2term | Maps free-text descriptions to ontology terms. | Candidate generation, ontology caching, lexical and semantic mapping patterns, thresholds. | Biomedical orientation; not a reversible document projection or bilingual BM25 pipeline. |
| Open Semantic Entity Search API — https://github.com/opensemanticsearch/open-semantic-entity-search-api | Links labels to IDs/URIs, normalizes aliases to preferred labels, returns candidates for disambiguation. | Entity-linking REST pattern and preferred-label normalization. | Entity-centric and Solr-oriented; does not preserve proposition operators. |
| MetaTerm — https://github.com/diegoberaldin/MetaTerm | Concept-oriented, multilingual terminology management. | Multiple terms per concept and language; terminology curation UI concepts. | Terminology authoring, not corpus parsing or retrieval evaluation. |
| Terminologue — https://github.com/gaois/terminologue | Open-source terminology-management platform. | Human curation, publication, permissions, termbase workflow. | Not a normalizer or retrieval pipeline. |
| VocBench — https://vocbench.uniroma2.it/ | Multilingual collaborative management of SKOS, SKOS-XL, OntoLex, OWL, and RDF. | Mature governance, roles, validation, import/export, linked-data publication. | Operationally heavy for an MVP. Suitable after the registry becomes organizational infrastructure. |
| Skosmos — https://github.com/NatLibFi/Skosmos | SKOS vocabulary browser and publication interface. | Search and browse a multilingual concept scheme. | Primarily read/publish rather than perform normalization. |
| OpenRefine — https://openrefine.org/docs/manual/reconciling | Interactive reconciliation and review of candidate entity matches. | High-value workbench for corpus-derived term candidates and ambiguity review. | Row-oriented data-cleaning workflow, not real-time document normalization. |
| Enelvo — https://github.com/thalesbertaglia/enelvo | Normalizes noisy user-generated Portuguese spelling, slang, acronyms, and names. | Portuguese surface-form cleanup before concept matching. | Orthographic/noise normalization, not semantic canonicalization. Use only as an optional front-end stage. |
| ASD-STE100 agent skill — https://github.com/danyuchn/asd-ste100-skill | Agent instructions that apply STE-inspired simplification while preserving facts and scope. | Good pattern for a bounded `SKILL.md` and explicit non-compliance disclaimer. | English rewriting skill; no concept registry or search projection. |
| ASD-STE100 vocabulary library — https://github.com/dfch/biz.dfch.AsdSte100Vocab | Python representation of an Issue 9-compatible vocabulary with technical nouns and verbs. | Possible future adapter for exact STE vocabulary checks. | Not a bilingual semantic registry and must still respect ASD licensing/authority limits. |
| Oxygen ASD-STE100 checker add-on — https://www.oxygenxml.com/addons/oxygen-terminology-checker-asd-ste100-styleguide.html | Terminology and rule-checking implementation for XML authoring. | Example of rule/checker separation and project terminology. | Authoring checker rather than ingestion normalizer. |

## 3. Concept-normalization research patterns

| Work | Main lesson for this project |
|---|---|
| Miftahutdinov & Tutubalina, “Deep Neural Models for Medical Concept Normalization in User-Generated Texts” — https://aclanthology.org/P19-2055/ | Concept normalization is formally treated as mapping a free-form mention to a controlled-vocabulary concept. Contextual semantic representations improve difficult mappings. |
| Gonçalves et al., “The text2term tool to map free-text descriptions of biomedical terms to ontologies” — https://doi.org/10.1093/database/baae119 | A practical mapper should support several input formats, cached ontologies, multiple matching methods, thresholds, and inspectable outputs. |
| Wajsbürt et al., multilingual medical concept normalization — https://github.com/percevalw/mlg-norm | Multilingual terminologies and contextual embeddings can bridge lexical variation across a non-English language and standardized concepts. |
| “Using SKOS vocabularies for improving web search” — https://doi.org/10.1145/2487788.2488159 | SKOS labels and semantic relations can support term expansion and retrieval scoring. |
| “A Process for Building Domain Specific Thesauri for Query Expansion” — https://doi.org/10.1145/3474624.3477057 | Domain thesauri must be built and evaluated as retrieval resources rather than assumed to help. |
| “Query expansion techniques for information retrieval: a survey” — https://doi.org/10.1016/j.ipm.2019.05.009 | Expansion can improve recall but is sensitive to term selection, ambiguity, weighting, and corpus/query characteristics. |

## 4. Search and evaluation stack

| Tool/reference | Role | Recommended use |
|---|---|---|
| Elasticsearch `synonym_graph` — https://www.elastic.co/docs/reference/text-analysis/analysis-synonym-graph-tokenfilter | Correct multi-token synonym graphs in a search analyzer. | Export approved aliases to concept tokens. Prefer search-time application during early iteration. |
| Elasticsearch synonym management — https://www.elastic.co/docs/solutions/search/full-text/search-with-synonyms | Synonym-set configuration and analyzer testing. | Keep the governed registry as source of truth and generate search-engine rules. |
| Elasticsearch RRF — https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion | Combines result sets with different scoring scales. | Benchmark raw BM25, concept BM25, and dense retrieval as separate lanes, then fuse rankings. |
| Pyserini — https://github.com/castorini/pyserini | Reproducible sparse, dense, and hybrid information-retrieval experiments. | Use for an external benchmark after the dependency-free smoke harness. |
| VectorChord-BM25 — https://github.com/tensorchord/VectorChord-bm25 | Native PostgreSQL BM25 with configurable tokenization. | Relevant when the production stack should remain PostgreSQL-centric. Verify maturity and benchmark before adoption. |

## 5. Agent and validation frameworks

| Framework | Use here | Adoption rule |
|---|---|---|
| Plain Python state machine | Deterministic stages, bounded retries, explicit statuses, easy tests. | Default for the MVP. |
| Instructor — https://python.useinstructor.com/ | Typed resolver output, schema validation, allow-list enforcement, controlled retries. | Use only for unresolved spans. Keep `max_retries` small and log every decision. |
| LangGraph — https://docs.langchain.com/oss/python/langgraph/overview | Durable execution, human-in-the-loop interrupts, persistence, deterministic and LLM nodes. | Add when review queues must survive process restarts or run across workers. It is unnecessary for the local MVP. |
| DSPy — https://dspy.ai/ | Structured signatures and metric-driven prompt/program optimization. | Add only after a labeled mapping dataset and a reliable metric exist. Never optimize against a purely synthetic set. |

## 6. RAG and hallucination evidence

The proposed normalizer targets one failure source: lexical mismatch in retrieval. Better retrieval can reduce downstream unsupported answers when the relevant source was previously missed. It cannot guarantee faithful generation.

- Lynx identifies retrieval failure as a source of downstream hallucination when the supplied context lacks sufficient information: https://arxiv.org/html/2407.08488v2
- RAG-X separates retriever success, context use, and grounded generation; aggregate answer accuracy can hide ungrounded answers: https://arxiv.org/html/2603.03541v1
- A 2026 attribution survey notes that RAG can improve grounding while introducing failures from retriever-generator interaction: https://arxiv.org/html/2601.19927v1

Therefore, measure these layers independently:

1. mapping correctness;
2. retrieval relevance;
3. answer support and citation correctness.

## 7. Community discussions — anecdotal, not evidence

- Reddit, “BM25 + Taxonomy for domain specific application”: https://www.reddit.com/r/Rag/comments/1uh4e46/bm25_taxonomy_for_domain_specific_application/

The useful community pattern is to preserve the raw field, add canonical/alias/entity fields, normalize queries, tune boosts, and compare by query class. This aligns with the architecture here, but it is not peer-reviewed evidence.

## 8. Final selection for this implementation

### Use now

- SKOS-inspired JSON registry with stable concept IDs.
- One preferred label per language plus alternative, hidden, and explicit surface forms.
- Immutable source and offset-level provenance.
- Deterministic longest-match candidate generation and per-occurrence local context scoring.
- Abstention for ambiguous aliases.
- Optional Instructor resolver restricted to supplied candidates.
- Raw, conservative canonical-text, concept-token, operator-token, and expanded retrieval fields.
- Dependency-free BM25 regression harness.
- SKOS and Elasticsearch synonym exports.

### Add after a real gold set exists

- OpenRefine/Reconciliation API review service.
- spaCy or Stanza morphology and dependency parsing.
- multilingual candidate embeddings and a reranker.
- Pyserini comparison and dense/hybrid RRF baselines.
- LangGraph persistence and human-review queues.
- DSPy optimization.
- VocBench or Terminologue as the governed terminology-authoring system.

### Do not do

- Replace or discard the source text.
- Translate all text to one language and treat the translation as authoritative.
- Use a global synonym map without part of speech, sense, domain, and counterexamples.
- let an LLM invent concept IDs or silently approve terminology.
- claim hallucination reduction from retrieval metrics alone.
- optimize only on generated or deliberately favorable examples.

## 9. Adjacent semantic representations evaluated

| Reference | Why it is relevant | Why it is not the primary runtime representation |
|---|---|---|
| Natural Semantic Metalanguage (NSM) — https://doi.org/10.1017/9781108989275.011 | This is the closest linguistic precedent to the idea of expressing complex meanings through a small cross-linguistic inventory. Current descriptions use 65 semantic primitives plus universal syntactic patterns. | It is a method for semantic explication, not an operational bilingual term linker or retrieval index. Explications can become long, context-sensitive, and expensive to produce automatically. Use it as a conceptual check on primitive meanings, not as the first BM25 field. |
| Abstract Meaning Representation guidelines — https://github.com/amrisi/amr-guidelines | Represents “who does what to whom” as concepts and relations, including predicate senses, roles, negation, quantities, and other operators. | The official guidelines explicitly state that AMR is closer to English and is not an interlingua. Automatic parsing adds another error layer. Consider a proposition graph only after lexical canonicalization is measured. |
| Grammatical Framework — https://www.grammaticalframework.org/ and https://github.com/GrammaticalFramework/gf-core | Separates an abstract syntax from language-specific concrete syntaxes and supports parsing, generation, translation, type checking, and paraphrasing. | It is strongest when the accepted language fragment and domain grammar are designed in advance. Arbitrary documentation and comments will frequently fall outside a controlled grammar. |
| Attempto Controlled English / ACE-in-GF — https://github.com/Attempto/ACE-in-GF and https://github.com/Attempto/APE | Demonstrates deterministic parsing of a restricted controlled language and multilingual realization of a subset through GF. | It is useful for future controlled authoring, not as a drop-in parser for unrestricted English and Portuguese. The ACE-in-GF project also does not implement the semantic DRS mapping. |

## 10. Retrieval evidence for vocabulary mismatch and expansion

| Work | Main lesson |
|---|---|
| Li et al., “Query Expansion in the Age of Pre-trained and Large Language Models” — https://arxiv.org/html/2509.07794v3 | Query expansion remains a primary response to vocabulary mismatch, but injection point, expansion source, control, and evaluation all matter. |
| Lei et al., “Corpus-Steered Query Expansion with Large Language Models” — https://aclanthology.org/2024.eacl-short.34/ | Corpus-grounded expansion can improve BM25 without training and limits unsupported expansion terms. This supports restricting candidate concepts to the governed registry and corpus. |
| Weller et al., “When do Generative Query and Document Expansions Fail?” — https://aclanthology.org/2024.findings-eacl.134/ | Expansion is not universally beneficial. Gains depend on retriever, domain, and query type, so raw, canonical, and expanded lanes must be evaluated separately. |
| Valentini et al., “Cross-Lingual Information Retrieval of Scientific Documents” — https://aclanthology.org/2025.mrl-main.16/ | Cross-language retrieval should be benchmarked explicitly; translation-based sparse retrieval can remain competitive, but translation choices materially affect effectiveness. |

These alternatives reinforce the same implementation decision: begin with reversible concept linking and controlled retrieval projections. Add deep semantic graphs or controlled multilingual generation only when a measured failure class justifies the extra complexity.
