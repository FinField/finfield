"""FinFieldKnitweb — the domain plugin that knits FinFacts into the P2P web.

Follows the knitweb plugin contract (see pulse `src/knitweb/knitwebs/`):
1. emit only sound records — gated on the FinField invariant (integer value,
   traceable source, valid period) before signing;
2. signed records are integer-only and deterministically ordered, so every
   node that ingests the same filing mints the same CIDv1;
3. records are signed with the publisher's key (fabric.attest), making
   authorship of every published financial fact non-repudiable.

Requires the `knitweb` package (the pulse runtime). Everything else in
finfield is stdlib-only; this module is the optional P2P edge.
"""
from __future__ import annotations

from typing import Optional

from .model import Entity, FactSet, FinFact

try:  # optional dependency: pip install knitweb / PYTHONPATH to a pulse checkout
    from knitweb.core import canonical, crypto
    from knitweb.fabric.attest import Attestation, attest
    from knitweb.fabric.web import Web

    HAS_KNITWEB = True
except ImportError:  # pragma: no cover
    HAS_KNITWEB = False

KIND_FACT = "finfact-record"
KIND_ENTITY = "finfield-entity"


class InvariantError(ValueError):
    pass


def check_fact(fact: FinFact) -> None:
    """FinField domain invariant: no fact without an exact value, a unit,
    a dated period, and a traceable source."""
    if not isinstance(fact.value, int) or isinstance(fact.value, bool):
        raise InvariantError(f"non-integer value for {fact.concept}")
    if not isinstance(fact.scale, int) or fact.scale < 0:
        raise InvariantError(f"bad scale for {fact.concept}")
    if not fact.unit:
        raise InvariantError(f"missing unit for {fact.concept}")
    if not fact.period.end:
        raise InvariantError(f"missing period end for {fact.concept}")
    if not fact.source.kind:
        raise InvariantError(f"missing source for {fact.concept}")
    if fact.source.kind != "finfield-derived" and not fact.source.ref:
        raise InvariantError(f"missing source ref for {fact.concept}")
    if fact.source.kind == "finfield-derived" and not fact.derived_from:
        raise InvariantError(f"derived fact without inputs: {fact.concept}")


class FinFieldKnitweb:
    """Sign and weave financial facts. One instance per publishing key."""

    KIND = KIND_FACT

    def __init__(self, author_priv: str) -> None:
        if not HAS_KNITWEB:
            raise ImportError("knitweb (pulse) is required for P2P publishing")
        self._priv = author_priv
        self.author_pub = crypto.public_from_private(author_priv)
        self.address = crypto.address(self.author_pub)

    # -- records -----------------------------------------------------------
    def entity_record(self, entity: Entity) -> dict:
        rec = {"kind": KIND_ENTITY, "ticker": entity.ticker, "author": self.address}
        for key in ("name", "country", "cik", "lei", "figi"):
            val = getattr(entity, key)
            if val:
                rec[key] = val
        canonical.encode(rec)  # fail fast if non-canonical
        return rec

    def to_record(self, fact: FinFact, derived_from_cids: tuple = ()) -> dict:
        """Integer-only canonical record for one fact.

        `derived_from_cids` are knitweb CIDs of already-woven input records
        (the fact's own `derived_from` holds standalone ff1 hashes).
        """
        period = {"end": fact.period.end}
        if fact.period.start:
            period["start"] = fact.period.start
        if fact.period.fiscal_year is not None:
            period["fy"] = fact.period.fiscal_year
        if fact.period.fiscal_period:
            period["fp"] = fact.period.fiscal_period
        rec = {
            "kind": self.KIND,
            "entity": fact.entity_id,
            "concept": fact.concept,
            "value": fact.value,
            "scale": fact.scale,
            "unit": fact.unit,
            "period": period,
            "source": {"kind": fact.source.kind, "ref": fact.source.ref, "fetched": fact.source.fetched},
            "derived_from": sorted(derived_from_cids),
            "author": self.address,
        }
        canonical.encode(rec)
        return rec

    # -- emit / weave --------------------------------------------------------
    def emit(self, fact: FinFact, derived_from_cids: tuple = ()) -> "Attestation":
        check_fact(fact)
        return attest(self.to_record(fact, derived_from_cids), self._priv, author_field="author")

    def weave(self, fact: FinFact, web: "Web", derived_from_cids: tuple = ()) -> tuple[str, "Attestation"]:
        att = self.emit(fact, derived_from_cids)
        return web.weave(att.record), att

    def weave_entity(self, entity: Entity, web: "Web") -> str:
        rec = self.entity_record(entity)
        att = attest(rec, self._priv, author_field="author")
        return web.weave(att.record)

    def weave_factset(
        self, fs: FactSet, web: "Web", derived: Optional[list] = None
    ) -> dict:
        """Weave an entity, its base facts, and derived facts, with edges:
        fact --about--> entity, derived --derived-from--> input fact.

        Returns {"entity": cid, "facts": {ff1_cid: knit_cid}, "attestations": [...]}.
        """
        entity_cid = self.weave_entity(fs.entity, web)
        knit_cid_by_ff1: dict[str, str] = {}
        atts = []
        for fact in fs.facts:
            kcid, att = self.weave(fact, web)
            knit_cid_by_ff1[fact.cid] = kcid
            atts.append(att)
            web.link(kcid, entity_cid, "about")
        for fact in derived or []:
            inputs = tuple(knit_cid_by_ff1[c] for c in fact.derived_from if c in knit_cid_by_ff1)
            kcid, att = self.weave(fact, web, derived_from_cids=inputs)
            knit_cid_by_ff1[fact.cid] = kcid
            atts.append(att)
            web.link(kcid, entity_cid, "about")
            for input_cid in inputs:
                web.link(kcid, input_cid, "derived-from")
        return {"entity": entity_cid, "facts": knit_cid_by_ff1, "attestations": atts}
