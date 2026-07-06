"""Smart layer: derived concepts computed from base facts.

Every derived fact records the CIDs of its inputs in `derived_from`, so the
chain from a headline ratio back to the audited filing is machine-checkable —
the thing no mainstream financial website gives you.

All arithmetic is exact (scaled integers / Decimal); ratios are emitted at
scale 6 (micro-units).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from ..model import FactSet, FinFact, Period, Source

DERIVED = Source(kind="finfield-derived", ref="finfield.smart.derive")
RATIO_SCALE = 6

# concepts treated as the canonical income-statement lines (first match wins)
REVENUE = (
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:Revenues",
    "us-gaap:SalesRevenueNet",
)
NET_INCOME = ("us-gaap:NetIncomeLoss",)


def _days(f: FinFact) -> int:
    s, e = date.fromisoformat(f.period.start), date.fromisoformat(f.period.end)
    return (e - s).days


def _quarterly(fs: FactSet, concepts: tuple) -> list[FinFact]:
    """Duration facts of ~one quarter for the first concept that has them."""
    for concept in concepts:
        rows = [
            f
            for f in fs.facts
            if f.concept == concept
            and f.period.start
            and f.period.fiscal_period in ("Q1", "Q2", "Q3", "Q4")
            and _days(f) <= 100
        ]
        if rows:
            # latest restatement wins per period (accessions sort chronologically)
            dedup = {
                (f.period.start, f.period.end): f
                for f in sorted(rows, key=lambda f: f.source.ref)
            }
            return sorted(dedup.values(), key=lambda f: f.period.end)
    return []


def _ratio_fact(entity_id: str, concept: str, numer: FinFact, denom: FinFact, period: Period) -> FinFact:
    ratio = numer.decimal / denom.decimal
    return FinFact(
        entity_id=entity_id,
        concept=concept,
        value=int((ratio * 10**RATIO_SCALE).to_integral_value()),
        scale=RATIO_SCALE,
        unit="pure",
        period=period,
        source=DERIVED,
        derived_from=(numer.cid, denom.cid),
    )


def ttm(fs: FactSet, concepts: tuple, out_concept: str) -> Optional[FinFact]:
    """Trailing-twelve-months sum of the last four distinct quarters."""
    q = _quarterly(fs, concepts)
    if len(q) < 4:
        return None
    last4 = q[-4:]
    common = max(f.scale for f in last4)
    total = sum(f.value * 10 ** (common - f.scale) for f in last4)
    return FinFact(
        entity_id=fs.entity.entity_id,
        concept=out_concept,
        value=total,
        scale=common,
        unit=last4[-1].unit,
        period=Period(start=last4[0].period.start, end=last4[-1].period.end),
        source=DERIVED,
        derived_from=tuple(f.cid for f in last4),
    )


def margin(numer: Optional[FinFact], denom: Optional[FinFact], out_concept: str) -> Optional[FinFact]:
    if not numer or not denom or denom.value == 0:
        return None
    return _ratio_fact(numer.entity_id, out_concept, numer, denom, numer.period)


def yoy_growth(fs: FactSet, concepts: tuple, out_concept: str) -> Optional[FinFact]:
    """Year-over-year growth of the most recent quarter vs the same quarter last year."""
    q = _quarterly(fs, concepts)
    if not q:
        return None
    by_fp = defaultdict(list)
    for f in q:
        by_fp[f.period.fiscal_period].append(f)
    latest = q[-1]
    same_fp = sorted(by_fp[latest.period.fiscal_period], key=lambda f: f.period.end)
    if len(same_fp) < 2 or same_fp[-2].value == 0:
        return None
    prev = same_fp[-2]
    growth = latest.decimal / prev.decimal - Decimal(1)
    return FinFact(
        entity_id=latest.entity_id,
        concept=out_concept,
        value=int((growth * 10**RATIO_SCALE).to_integral_value()),
        scale=RATIO_SCALE,
        unit="pure",
        period=latest.period,
        source=DERIVED,
        derived_from=(latest.cid, prev.cid),
    )


def derive_all(fs: FactSet) -> list[FinFact]:
    """Standard smart pack: TTM revenue/income, net margin, YoY growth."""
    rev_ttm = ttm(fs, REVENUE, "finfield:revenue_ttm")
    ni_ttm = ttm(fs, NET_INCOME, "finfield:net_income_ttm")
    out = [
        rev_ttm,
        ni_ttm,
        margin(ni_ttm, rev_ttm, "finfield:net_margin_ttm"),
        yoy_growth(fs, REVENUE, "finfield:revenue_yoy"),
        yoy_growth(fs, NET_INCOME, "finfield:net_income_yoy"),
    ]
    return [f for f in out if f is not None]
