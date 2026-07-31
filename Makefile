PYTHON ?= python3
RUN = PYTHONPATH=src $(PYTHON) -m semantic_normalizer

.PHONY: test validate-registry normalize-demo query-demo evaluate export manifest release check

test:
	PYTHONPATH=src $(PYTHON) -m pytest tests -q

validate-registry:
	$(RUN) validate-registry

normalize-demo:
	$(RUN) normalize --text "O operador deve começar o servidor APP-01."

query-demo:
	$(RUN) query --text "Como iniciar o servidor?"

evaluate:
	$(RUN) evaluate tests/fixtures/dev_retrieval.json

export:
	$(RUN) export skos --output exports/concepts.ttl
	$(RUN) export synonym-graph --output exports/synonyms.txt

# Recut MANIFEST.json from the tree. Run after ANY file change: the manifest is evidence,
# and tests/test_manifest_integrity.py fails until it matches what is on disk.
manifest:
	$(PYTHON) scripts/cut_manifest.py

release:
	PYTHONPATH=src $(PYTHON) scripts/build_release.py --json

# The correct order for a change that touches files and docs.
check: release manifest test

# Delivery gate. `check` alone was not enough: three review rounds in a row caught a stale
# MANIFEST.json because a document was edited AFTER the manifest was cut, and ordering inside
# one `make check` cannot see an edit made after it finished. So this re-cuts once more and
# fails if that produces any change — which is exactly the "something was edited after the
# last cut" condition. Run it as the literal last command before declaring the work done.
#
# It re-cuts release-manifest.json and checksums.sha256 too, because covering only MANIFEST.json
# left the same hole one file over and it was already through it: a fourth review found the
# committed release manifest recording docs/plan-cga-domain-lexicon-0.4.0.md at 27096 bytes when
# the committed file is 27092 — a four-byte edit made after the release was cut, shipped by a gate
# whose whole purpose is catching that. A guard that watches one of three generated artifacts is
# not a guard against stale artifacts.
#
# The re-cut runs release BEFORE manifest, the same order as `check`. MANIFEST.json hashes
# release-manifest.json, so cutting the manifest first and the release second leaves the manifest
# describing a file that no longer exists in that form — the gate would then fail every run on a
# clean tree, which is a broken gate rather than a strict one.
#
# And it cuts TWICE: once to settle, once to compare. `check` ends with `test`, and two tests
# regenerate reports as part of what they check, so the artifacts are legitimately stale the
# moment `check` finishes. Comparing against that state fails every run and says nothing. The
# settle pass absorbs what the tests wrote; the compare pass is then answering the question the
# gate is actually for — did anything change that regenerating does not explain.
deliver: check
	@PYTHONPATH=src $(PYTHON) scripts/build_release.py --json > /dev/null
	@$(PYTHON) scripts/cut_manifest.py > /dev/null
	@cp MANIFEST.json .manifest.before
	@cp reports/release-manifest.json .release.before
	@cp checksums.sha256 .checksums.before
	@PYTHONPATH=src $(PYTHON) scripts/build_release.py --json > /dev/null
	@$(PYTHON) scripts/cut_manifest.py > /dev/null
	@if cmp -s MANIFEST.json .manifest.before \
		&& cmp -s reports/release-manifest.json .release.before \
		&& cmp -s checksums.sha256 .checksums.before; then \
		rm -f .manifest.before .release.before .checksums.before; \
		echo "deliver: tree, manifest and tests agree"; \
	else \
		rm -f .manifest.before .release.before .checksums.before; \
		echo "A generated artifact changed on a second cut: a file was edited after 'make check'."; \
		echo "The tree is fine — re-run 'make deliver' as the literal last command."; \
		exit 1; \
	fi
