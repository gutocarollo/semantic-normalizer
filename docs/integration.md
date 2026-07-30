# Retrieval Integration

## 1. Generic document record

```json
{
  "document_id": "manual-17#section-4.2",
  "text_raw": "O operador deve começar o servidor APP-01.",
  "text_canonical": "O operador deve iniciar o servidor APP-01.",
  "concept_tokens": [
    "c__role__operator",
    "c__action__start",
    "c__system__server"
  ],
  "operator_tokens": ["modality__obligation"],
  "registry_version": "0.1.0",
  "normalization_status": "accepted",
  "source_uri": "...",
  "source_offsets": [120, 165]
}
```

## 2. Fielded BM25

Use separate boosts rather than one concatenated field. A starting hypothesis is:

```text
text_raw^3
text_canonical^2
concept_tokens^2
operator_tokens^1
```

Tune these values. Exact identifiers and quoted language usually justify the strongest raw-field boost.

## 3. Query processing

```text
query_raw
  -> same registry/version
  -> query_canonical
  -> query_concept_tokens
  -> query_operator_tokens
  -> fielded lexical query
```

Log expansions so failed retrieval can be reproduced.

## 4. Elasticsearch synonym export

The command:

```bash
semantic-normalizer export-synonyms --output exports/synonyms.txt
```

creates explicit rules such as:

```text
begin => c__action__start
começar => c__action__start
iniciar => c__action__start
start => c__action__start
```

Use these rules in a search analyzer. Keep the original text field on an analyzer without destructive synonym replacement.

The exporter omits aliases that map to multiple concepts. For example, a context-dependent form such as `remove` cannot safely become one global synonym rule. Process those forms through the normalizer, which can use the local object and negative context or abstain.

## 5. Grep and ripgrep

Write a sidecar projection for each source file:

```text
manual.md
manual.md.semantic.txt
```

Example sidecar:

```text
source: manual.md:42
concepts: c__role__operator c__action__start c__system__server
canonical: O operador deve iniciar o servidor APP-01.
```

Search concepts across languages:

```bash
rg --fixed-strings 'c__action__start' docs/**/*.semantic.txt
```

Search exact source language separately:

```bash
rg --fixed-strings 'APP-01' docs/
```

## 6. Hybrid retrieval

Keep lexical and vector rankings independent, then fuse ranks. This prevents one scoring scale from dominating the other.

A normalizer can improve the lexical leg but should not replace dense retrieval tests. Dense search can recover paraphrases outside the registry; lexical fields remain strong for identifiers, numbers, defined terms, and auditable matches.

## 7. Indexing policy by status

```text
accepted -> raw + canonical + concept + vector
review   -> raw + vector; canonical field optional and down-weighted
rejected -> raw only; open processing defect
```
