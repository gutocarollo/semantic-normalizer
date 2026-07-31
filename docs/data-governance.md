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

- ASD-STE100: normative reference for English technical controlled writing. Use
  it as a selection signal (which terms are essential) and as a record template;
  it is English-only, so every Portuguese label is authored here regardless.
  Definitions and examples in this registry are original.
- CILI/Global WordNet: candidate interlingual identity namespace.
- OpenWN-PT/OMW: candidate Portuguese lexicalization, subject to the exact
  release license and attribution.
- Plain-language guidance: editorial constraint, not a concept identifier.

The runtime remains dependency-free until an import demonstrates measurable
retrieval or coverage benefit against a gold corpus.
