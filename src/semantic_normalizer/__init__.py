"""Bilingual semantic normalizer.

The package keeps source text immutable and emits a parallel canonical projection.
"""

__version__ = "0.2.0"

from .normalizer import SemanticNormalizer
from .registry import ConceptRegistry

__all__ = ["ConceptRegistry", "SemanticNormalizer"]
