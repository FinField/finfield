"""Knitweb plugin tests — run when the pulse runtime is importable."""
import pytest

from finfield.knit import HAS_KNITWEB, InvariantError, check_fact
from finfield.model import Entity, FactSet, FinFact, Period, Source
from finfield.smart.derive import derive_all
from tests.test_derive import _fs

requires_knitweb = pytest.mark.skipif(not HAS_KNITWEB, reason="knitweb (pulse) not installed")


def _fact(**kw):
    base = dict(
        entity_id="ticker:TEST US",
        concept="us-gaap:Revenues",
        value=1000,
        unit="USD",
        period=Period(end="2025-03-31", start="2025-01-01", fiscal_year=2025, fiscal_period="Q1"),
        source=Source(kind="sec-companyfacts", ref="a1", fetched="2026-07-06"),
    )
    base.update(kw)
    return FinFact(**base)


# 1 — invariant rejects unsound facts
def test_invariant():
    check_fact(_fact())
    with pytest.raises(InvariantError):
        check_fact(_fact(unit=""))
    with pytest.raises(InvariantError):
        check_fact(_fact(source=Source(kind="sec-companyfacts", ref="")))
    with pytest.raises(InvariantError):
        check_fact(_fact(source=Source(kind="finfield-derived", ref="x")))  # derived w/o inputs


@requires_knitweb
def test_weave_deterministic():
    from knitweb.core import crypto
    from knitweb.fabric.web import Web
    from finfield.knit import FinFieldKnitweb

    priv, _ = crypto.generate_keypair()
    kw = FinFieldKnitweb(priv)
    web = Web()
    cid1, att = kw.weave(_fact(), web)
    cid2, _ = kw.weave(_fact(), web)
    assert cid1 == cid2  # same fact, same key -> same CID, idempotent weave
    assert att.verify(author_field="author")
    assert cid1.startswith("b")  # CIDv1 multibase base32


@requires_knitweb
def test_weave_factset_edges():
    from knitweb.core import crypto
    from knitweb.fabric.web import Web
    from finfield.knit import FinFieldKnitweb

    fs = _fs([100, 200, 300, 400, 500], [10, 20, 30, 40, 50])
    derived = derive_all(fs)
    kw = FinFieldKnitweb(crypto.generate_keypair()[0])
    web = Web()
    result = kw.weave_factset(fs, web, derived=derived)
    assert len(result["facts"]) == len(fs.facts) + len(derived)
    # a derived record links back to woven inputs
    ttm_ff1 = next(f for f in derived if f.concept == "finfield:revenue_ttm")
    ttm_cid = result["facts"][ttm_ff1.cid]
    rels = {e.rel for e in web.outgoing_edges(ttm_cid)}
    assert rels == {"about", "derived-from"}
    # every woven record passes the read-side audit
    from knitweb.fabric.attest import check_record

    for att in result["attestations"]:
        from knitweb.core import canonical

        chk = check_record(att.record, canonical.cid(att.record), att.author_pub, att.sig, author_field="author")
        assert chk.ok, chk.reason
