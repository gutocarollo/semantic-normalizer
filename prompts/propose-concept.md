# Terminology-curation proposal

Use this prompt offline during registry curation. Never let this prompt modify the production registry automatically.

## Task

Group observed English and Portuguese lexical forms by meaning. Propose one language-independent concept only when all forms preserve the same domain proposition.

## Required checks

- Separate homonyms and polysemous uses.
- Separate broader, narrower, related, and approximately equivalent meanings.
- Record part of speech and domain scope.
- Select one preferred label per language.
- Put accepted synonyms and abbreviations in alternative labels.
- Put misspellings and search-only forms in hidden labels.
- Record source examples and counterexamples.
- Preserve official company, legal, safety, and product terminology.
- Reject a proposal when the evidence is insufficient.

## Output schema

```json
{
  "decision": "propose | reject | needs_review",
  "concept_id_candidate": "namespace.local_name",
  "preferred_labels": {"en": "", "pt": ""},
  "alternative_labels": {"en": [], "pt": []},
  "hidden_labels": {"en": [], "pt": []},
  "definition": {"en": "", "pt": ""},
  "part_of_speech": "",
  "domains": [],
  "positive_examples": [],
  "counterexamples": [],
  "source_authorities": [],
  "rationale": ""
}
```
