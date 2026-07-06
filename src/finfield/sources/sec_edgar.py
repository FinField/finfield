"""SEC EDGAR companyfacts adapter.

The SEC publishes every XBRL fact ever filed by every US-listed reporter as
open, public-domain JSON (data.sec.gov). This is the highest-quality free
fundamentals source that exists: audited filings, full history, per-fact
accession provenance. FinField normalizes it into FinFacts.

Rate limits: SEC asks for <=10 req/s and a descriptive User-Agent.
Bulk mode: companyfacts.zip (~1.3 GB) carries the whole corpus for the
full-universe ingest without per-company HTTP calls.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

from ..model import Entity, FactSet, FinFact, Period, Source, to_scaled
from .base import FactSource

USER_AGENT = "FinField/0.1 (open financial facts; contact: develuse@gmail.com)"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"


def iter_bulk(zip_path: Path) -> Iterator[tuple[int, dict]]:
    """Stream (cik, companyfacts_doc) from the SEC bulk archive.

    The ~1.4 GB companyfacts.zip carries the whole XBRL corpus (20k+
    reporters); streaming from the zip avoids a 10 GB extraction.
    """
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.startswith("CIK") or not name.endswith(".json"):
                continue
            try:
                cik = int(name[3:-5])
                doc = json.loads(z.read(name))
            except (ValueError, json.JSONDecodeError):
                continue
            yield cik, doc


def _ssl_context() -> ssl.SSLContext:
    try:  # python.org macOS builds ship without system CA certs
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
        return resp.read()


class SecEdgarSource(FactSource):
    kind = "sec-companyfacts"

    def __init__(self, cache_dir: Optional[Path] = None, throttle: float = 0.12):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.throttle = throttle
        self._ticker_to_cik: Optional[dict[str, int]] = None

    # -- identity ---------------------------------------------------------
    def ticker_map(self) -> dict[str, int]:
        if self._ticker_to_cik is None:
            raw = None
            cache = self.cache_dir / "company_tickers.json" if self.cache_dir else None
            if cache and cache.exists():
                raw = cache.read_bytes()
            else:
                raw = _get(TICKER_MAP_URL)
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(raw)
            data = json.loads(raw)
            self._ticker_to_cik = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
        return self._ticker_to_cik

    def resolve_cik(self, entity: Entity) -> Optional[int]:
        if entity.cik:
            return int(entity.cik)
        # composite tickers are "SYM CC"; SEC covers US listings
        parts = entity.ticker.split()
        if len(parts) == 2 and parts[1] != "US":
            return None
        sym = parts[0].upper().replace("/", "-").replace(".", "-")
        return self.ticker_map().get(sym)

    def covers(self, entity: Entity) -> bool:
        return self.resolve_cik(entity) is not None

    # -- facts ------------------------------------------------------------
    def fetch(self, entity: Entity) -> Optional[FactSet]:
        cik = self.resolve_cik(entity)
        if cik is None:
            return None
        url = COMPANYFACTS_URL.format(cik=cik)
        cache = self.cache_dir / f"CIK{cik:010d}.json" if self.cache_dir else None
        if cache and cache.exists():
            raw = cache.read_bytes()
        else:
            time.sleep(self.throttle)
            try:
                raw = _get(url)
            except Exception:
                return None
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
        return self.normalize(entity, json.loads(raw))

    def normalize(self, entity: Entity, doc: dict) -> FactSet:
        """Turn one companyfacts document into a FactSet."""
        fs = FactSet(entity=entity)
        today = date.today().isoformat()
        for taxonomy, concepts in doc.get("facts", {}).items():
            for concept, body in concepts.items():
                for unit, observations in body.get("units", {}).items():
                    for ob in observations:
                        raw = ob.get("val")
                        if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
                            continue
                        try:
                            value, scale = to_scaled(raw)
                        except Exception:
                            continue
                        fs.add(
                            FinFact(
                                entity_id=entity.entity_id,
                                concept=f"{taxonomy}:{concept}",
                                value=value,
                                scale=scale,
                                unit=unit,
                                period=Period(
                                    end=ob.get("end", ""),
                                    start=ob.get("start"),
                                    fiscal_year=ob.get("fy"),
                                    fiscal_period=ob.get("fp"),
                                ),
                                source=Source(
                                    kind=self.kind,
                                    ref=ob.get("accn", ""),
                                    fetched=today,
                                ),
                            )
                        )
        return fs.dedupe()
