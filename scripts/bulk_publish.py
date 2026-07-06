#!/usr/bin/env python3
"""Bulk-publish the core fact pack for all SEC reporters — nothing stays local.

Streams the SEC companyfacts.zip (20k+ companies), extracts the latest
observation per core concept per company, signs each fact as a finfact
record, and appends everything to the sharded feed in a FinField/facts
working copy. The caller pushes the repo and deletes the working copy and
the zip: the data then lives on GitHub / finfield.github.io / 5mart.ml and
replicates node-to-node, not on this machine.

Usage:
    python3 scripts/bulk_publish.py <companyfacts.zip> <facts-repo-dir> [--limit N]
"""
import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from finfield.model import Entity, FactSet, FinFact, Period, Source, to_scaled
from finfield.publish import Publisher
from finfield.sources.sec_edgar import iter_bulk

# The core pack: identity, scale, income, balance, cashflow, per-share, float.
# dei:EntityPublicFloat is the free-float market cap (Fama-French base);
# shares outstanding are integers tied to their observation date.
CORE_CONCEPTS = {
    "dei:EntityCommonStockSharesOutstanding",
    "dei:EntityPublicFloat",
    "us-gaap:CommonStockSharesOutstanding",
    "us-gaap:CommonStockSharesIssued",
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:Revenues",
    "us-gaap:SalesRevenueNet",
    "us-gaap:NetIncomeLoss",
    "us-gaap:OperatingIncomeLoss",
    "us-gaap:OperatingExpenses",
    "us-gaap:CostOfRevenue",
    "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
    "us-gaap:Assets",
    "us-gaap:Liabilities",
    "us-gaap:StockholdersEquity",
    "us-gaap:CashAndCashEquivalentsAtCarryingValue",
    "us-gaap:EarningsPerShareBasic",
    "us-gaap:EarningsPerShareDiluted",
}


def latest_core_facts(entity: Entity, doc: dict, fetched: str) -> FactSet:
    """Latest observation per (core concept, unit) from one companyfacts doc."""
    fs = FactSet(entity=entity)
    for taxonomy, concepts in doc.get("facts", {}).items():
        for concept, body in concepts.items():
            qname = f"{taxonomy}:{concept}"
            if qname not in CORE_CONCEPTS:
                continue
            for unit, observations in body.get("units", {}).items():
                best = None
                for ob in observations:
                    if ob.get("val") is None or not ob.get("end"):
                        continue
                    key = (ob["end"], ob.get("accn", ""))
                    if best is None or key > (best["end"], best.get("accn", "")):
                        best = ob
                if best is None:
                    continue
                try:
                    value, scale = to_scaled(best["val"])
                except Exception:
                    continue
                fs.add(
                    FinFact(
                        entity_id=entity.entity_id,
                        concept=qname,
                        value=value,
                        scale=scale,
                        unit=unit,
                        period=Period(
                            end=best["end"],
                            start=best.get("start"),
                            fiscal_year=best.get("fy"),
                            fiscal_period=best.get("fp"),
                        ),
                        source=Source(kind="sec-companyfacts", ref=best.get("accn", ""), fetched=fetched),
                    )
                )
    return fs


def sec_ticker_map() -> dict[int, str]:
    """cik -> composite ticker, from the SEC's public map."""
    import json

    from finfield.sources.sec_edgar import _get, TICKER_MAP_URL

    data = json.loads(_get(TICKER_MAP_URL))
    out: dict[int, str] = {}
    for v in data.values():  # first listing per CIK wins (primary class)
        out.setdefault(int(v["cik_str"]), f"{v['ticker'].upper()} US")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path", type=Path)
    ap.add_argument("repo_dir", type=Path)
    ap.add_argument("--limit", type=int, default=0, help="stop after N companies (smoke test)")
    args = ap.parse_args()

    fetched = date.today().isoformat()
    tickers = sec_ticker_map()
    pub = Publisher(args.repo_dir)
    known = pub.store.known_cids()

    t0 = time.time()
    companies = facts = skipped = 0
    for cik, doc in iter_bulk(args.zip_path):
        ticker = tickers.get(cik)
        if ticker is None:
            skipped += 1  # funds/trusts without a listed ticker
            continue
        entity = Entity(ticker=ticker, name=doc.get("entityName", ""), cik=str(cik))
        fs = latest_core_facts(entity, doc, fetched)
        if not fs.facts:
            skipped += 1
            continue
        from knitweb.fabric.web import Web

        result = pub.kw.weave_factset(fs, Web())
        facts += pub.publish_records([a.record for a in result["attestations"]], known=known)
        companies += 1
        if companies % 1000 == 0:
            print(f"{companies} companies, {facts} records, {time.time()-t0:.0f}s", flush=True)
        if args.limit and companies >= args.limit:
            break

    head = pub.commit()
    print(f"DONE {companies} companies, {facts} records, {skipped} skipped, {time.time()-t0:.0f}s")
    print(f"head root={head['root'][:20]}… length={head['length']} publisher={pub.kw.address}")


if __name__ == "__main__":
    main()
