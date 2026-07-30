# Lexical Data Governance

## Two artifacts, two completeness claims

The concept registry is complete only for supported domain meanings. The
controlled lexicon is complete only relative to a declared corpus and a
versioned OOV review queue. Neither claim means “all English” or “all
Portuguese”.

## Required source record

Every imported candidate batch must record its source URL or local authority,
version, license, retrieval date, SHA-256, import command, and approval state.
`staged` records cannot emit automatic mappings. `approved` records require a
concept ID, EN/PT preferred forms, part of speech, positive example and a
confusing or negative example where ambiguity is possible.

## Reference choices

- ASD-STE100: local normative reference for English technical controlled
  writing; do not redistribute its dictionary without explicit authorization.
- CILI/Global WordNet: candidate interlingual identity namespace.
- OpenWN-PT/OMW: candidate Portuguese lexicalization, subject to the exact
  release license and attribution.
- Plain-language guidance: editorial constraint, not a concept identifier.

The runtime remains dependency-free until an import demonstrates measurable
retrieval or coverage benefit against a gold corpus.
