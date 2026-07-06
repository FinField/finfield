#!/usr/bin/env python3
"""Build the public-safe FinField universe from the local EDS dataset.

Reads the private EDS seed (Numerai ticker history + EDS master snapshots),
strips licensed identifier columns (SEDOL, ISIN, GICS, FactSet ids), and
emits data/universe.csv with only open, factual fields:

    ticker, country, name, active, first_seen, last_seen

The private seed never leaves the local machine; only this derived,
licence-clean universe is published.
"""
import csv
import sys
from pathlib import Path

SIGNALS = Path.home() / "Documents/Signals/Data"
NUMERAI = SIGNALS / "Identifiers/FMP/numerai_tickers_output.csv"
EDS_MASTER = SIGNALS / "EDS/EDS_20230824.csv"
OUT = Path(__file__).resolve().parent.parent / "src/finfield/data/universe.csv"


def main() -> None:
    seen: dict[str, dict] = {}
    with NUMERAI.open() as f:
        for row in csv.DictReader(f):
            t = row["ticker"].strip()
            if not t:
                continue
            rec = seen.setdefault(
                t, {"first_seen": row["start_date"], "last_seen": row["end_date"], "live": False}
            )
            if row["start_date"] and row["start_date"] < rec["first_seen"]:
                rec["first_seen"] = row["start_date"]
            if row["end_date"] and row["end_date"] > rec["last_seen"]:
                rec["last_seen"] = row["end_date"]
            if row["dataset"] == "live":
                rec["live"] = True

    names: dict[str, dict] = {}
    if EDS_MASTER.exists():
        with EDS_MASTER.open() as f:
            for row in csv.DictReader(f):
                t = (row.get("bloomberg_ticker") or "").strip()
                if t:
                    names[t] = {
                        "name": (row.get("Name") or "").strip(),
                        "country": (row.get("Country_of_domicile") or "").strip(),
                        "active": (row.get("Active") or "").strip(),
                    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "country", "name", "active", "first_seen", "last_seen"])
        for t in sorted(seen):
            rec = seen[t]
            meta = names.get(t, {})
            # country: prefer EDS master, else the exchange suffix of the composite ticker
            country = meta.get("country") or (t.split()[-1] if " " in t else "")
            active = meta.get("active") or ("1" if rec["live"] else "")
            w.writerow([t, country, meta.get("name", ""), active, rec["first_seen"], rec["last_seen"]])

    print(f"wrote {OUT} with {len(seen)} tickers")


if __name__ == "__main__":
    sys.exit(main())
