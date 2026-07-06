"""FinField — open, provenance-first financial facts for 20,000+ listed
companies, knitted into the knitweb P2P network.

Core is stdlib-only. P2P publishing (finfield.knit) needs the `knitweb`
(pulse) package.
"""
from .model import Entity, FactSet, FinFact, Period, Source, cid, canonical_json, to_scaled, from_scaled

__version__ = "0.1.0"
__all__ = [
    "Entity",
    "FactSet",
    "FinFact",
    "Period",
    "Source",
    "cid",
    "canonical_json",
    "to_scaled",
    "from_scaled",
    "__version__",
]
