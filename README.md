# FinField

**Open, provenance-first financial facts for 20,000+ listed companies, knitted into the [knitweb](https://knitweb.art) P2P network.**

Financial websites give you numbers. FinField gives you *facts*: every number carries its source (down to the SEC accession number of the audited filing), every derived metric carries the content-ids of its inputs, and every published record is signed and content-addressed — so the chain from a headline ratio back to the filing is machine-checkable, and any two nodes that ingest the same filing mint byte-identical records that converge over P2P without coordination.

Part of the KnitWeb hub-and-fields family (ChemField, githfield, ledgerfield). Landing: [finfield.github.io](https://finfield.github.io).

## Why this beats a financial website

| | Financial websites | FinField |
|---|---|---|
| Provenance | "source: company reports" | per-fact accession/URL + signed authorship |
| Derived metrics | opaque | `derived_from` CIDs — audit the arithmetic |
| Precision | floats, rounding | exact scaled integers (Decimal-clean) |
| Distribution | one vendor's API, rate-limited | P2P replication, content-addressed, no gatekeeper |
| Updates | trust the vendor | verify the CID, verify the signature, accept |
| Universe | paywalled screeners | open 20,698-company universe (EDS-derived, licence-clean) |

## Quick start

```bash
pip install -e ".[dev]"

finfield universe --count                 # 20,698 companies
finfield fetch "AAPL US"                  # all SEC XBRL facts, normalized
finfield facts "AAPL US" --concept us-gaap:Revenues
finfield smart "AAPL US"                  # TTM revenue/income, margins, YoY growth — with provenance
finfield knit  "AAPL US"                  # sign + weave into a knitweb Web (needs `knitweb`)
```

## Architecture

- **`finfield.model`** — `FinFact`: (entity, concept, value×10⁻ˢᶜᵃˡᵉ, unit, period, source, derived_from). Canonical JSON → deterministic `ff1:` content-id. Stdlib-only.
- **`finfield.universe`** — the 20k+ company universe (`data/universe.csv`), derived from the private EDS seed with licensed identifier schemes (SEDOL, ISIN, GICS) stripped. Only open identifiers ship: ticker, country, name; CIK/LEI/FIGI resolved at ingest.
- **`finfield.sources`** — source adapters. `SecEdgarSource` normalizes SEC XBRL companyfacts (public domain, audited, full history; bulk `companyfacts.zip` for the full-universe ingest). More adapters welcome (GLEIF, filings from other regulators).
- **`finfield.smart`** — derived concepts (`finfield:*` namespace): TTM aggregation, margins, YoY growth. Exact arithmetic, ratio scale 10⁻⁶, inputs recorded as CIDs.
- **`finfield.knit`** — the knitweb domain plugin (`FinFieldKnitweb`, kind `finfact-record`). Follows the pulse plugin contract: domain invariant gate → integer-only canonical CBOR record → secp256k1 attestation → `Web.weave` (CIDv1) with `about` / `derived-from` edges. Publish via signed feeds over the 5mart.ml relay.

### The FinField invariant

No fact enters the web without: an exact integer value, a unit, a dated period, and a traceable source; derived facts must name their inputs. `finfield.knit.check_fact` gates every `emit`.

### P2P updates

Records are content-addressed and signed. A new filing produces new facts with new CIDs; restatements coexist with the originals (latest accession wins in the smart layer). Peers accept a record iff the CID matches the canonical bytes and the signature matches the author field — trust-free adoption, no central updater.

## Data licensing

The public universe carries only factual, open fields. Licensed identifier schemes (SEDOL, ISIN redistribution, GICS, FactSet ids) are deliberately excluded and the private seed never leaves the maintainer's machine. SEC EDGAR data is US-government public domain.

## Roadmap

- Full-universe ingest from `companyfacts.zip` (all ~7k US reporters in one batch)
- GLEIF LEI + OpenFIGI identity resolution for the non-US 13k
- Cross-source reconciliation (independent sources agree → confidence facts)
- Price/valuation facts (PE, yields) once an open price source is wired
- Prediction-competition targets (Numerai Signals / Crypto) as derived fact packs

## Development

```bash
python3 -m pytest tests/ -q     # 21 property tests; knit tests need `knitweb`
python3 scripts/build_universe.py   # rebuild universe from the local EDS seed
```

Apache-2.0.
