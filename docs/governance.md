# Terminology Governance

## 1. Roles

| Role | Responsibility |
|---|---|
| Domain owner | approves technical meaning and preferred terminology |
| Terminologist or curator | maintains concept boundaries, labels, notes, and lifecycle |
| Retrieval engineer | evaluates indexing behavior and field weights |
| Language reviewer | reviews English-Portuguese equivalence and lexical gaps |
| Application owner | approves release thresholds and rollback |

One person can hold several roles in a small team, but approval authority must remain explicit.

## 2. Concept proposal record

Every new concept proposal should contain:

- proposed concept ID;
- English and Portuguese definitions;
- preferred label in each language;
- admitted and deprecated alternatives;
- part of speech;
- domain and scope note;
- positive examples;
- negative or confusing examples;
- broader and related concepts;
- source authority;
- proposer and approver;
- effective version and date.

## 3. Approval workflow

```text
observed corpus variant
    -> candidate cluster
    -> existing-concept reconciliation
        -> match: add reviewed lexical form
        -> no match: draft concept
    -> bilingual/domain review
    -> regression tests
    -> approval
    -> versioned release
```

## 4. Lifecycle

Use these states:

- `draft` — not used for automatic normalization;
- `approved` — eligible for deterministic mapping;
- `deprecated` — searchable for old content but never emitted as preferred output.

Never delete an ID that appeared in an indexed release. Redirect or mark it deprecated and provide migration metadata.

## 5. Ambiguous labels

The same lexical form can legitimately map to several concepts. Record:

- candidate concepts;
- discriminating context;
- positive examples;
- negative examples;
- minimum confidence;
- whether human review is mandatory.

Do not “solve” ambiguity by merging concepts whose definitions differ.

## 6. Versioning

Use semantic versioning for the registry:

- patch: spelling, metadata, or non-behavioral correction;
- minor: additive concepts or lexical forms with compatible behavior;
- major: changed concept scope, preferred identity, or mapping behavior.

Store `registry_version` and `registry_sha256` with every normalized record.

## 7. Audit data

Retain:

- source text hash;
- registry hash;
- mapping spans;
- selected candidates and rejected alternatives;
- deterministic and resolver scores;
- resolver model and prompt version;
- validator results;
- human decision and reviewer;
- retrieval regression result.
