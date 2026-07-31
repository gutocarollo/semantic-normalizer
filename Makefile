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
deliver: check
	@cp MANIFEST.json .manifest.before
	@$(PYTHON) scripts/cut_manifest.py > /dev/null
	@if cmp -s MANIFEST.json .manifest.before; then \
		rm -f .manifest.before; \
		echo "deliver: tree, manifest and tests agree"; \
	else \
		rm -f .manifest.before; \
		echo "MANIFEST.json changed on a second cut: a file was edited after 'make check'."; \
		echo "The tree is fine — re-run 'make deliver' as the literal last command."; \
		exit 1; \
	fi
