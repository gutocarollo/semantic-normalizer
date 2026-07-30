PYTHON ?= python3

.PHONY: test normalize-demo evaluate-demo validate-registry export-skos export-synonyms

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

normalize-demo:
	PYTHONPATH=src $(PYTHON) -m semantic_normalizer normalize \
		--text "O operador deve iniciar o servidor APP-01." --lang pt --pretty

evaluate-demo:
	PYTHONPATH=src $(PYTHON) -m semantic_normalizer evaluate \
		--documents examples/documents.jsonl --queries examples/queries.jsonl --k 1 3

validate-registry:
	PYTHONPATH=src $(PYTHON) -m semantic_normalizer validate-registry

export-skos:
	PYTHONPATH=src $(PYTHON) -m semantic_normalizer export-skos --output exports/concepts.ttl

export-synonyms:
	PYTHONPATH=src $(PYTHON) -m semantic_normalizer export-synonyms --output exports/synonyms.txt
