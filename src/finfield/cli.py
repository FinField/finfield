"""finfield CLI.

    finfield universe [--active] [--country CC]
    finfield fetch "AAPL US"          # pull all SEC facts, print summary
    finfield facts "AAPL US" --concept us-gaap:Revenues [--limit N]
    finfield smart "AAPL US"          # derived metrics with provenance
    finfield knit "AAPL US"           # sign + weave into a local web, print CIDs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import universe as universe_mod
from .model import Entity
from .sources.sec_edgar import SecEdgarSource
from .smart.derive import derive_all

CACHE = Path(os.environ.get("FINFIELD_CACHE", Path.home() / ".finfield/cache"))


def _entity(ticker: str) -> Entity:
    for e in universe_mod.iter_universe():
        if e.ticker == ticker:
            return e
    return Entity(ticker=ticker)


def _fetch(ticker: str):
    src = SecEdgarSource(cache_dir=CACHE)
    fs = src.fetch(_entity(ticker))
    if fs is None:
        print(f"no SEC facts for {ticker} (non-US listing or unknown ticker)", file=sys.stderr)
        sys.exit(1)
    return fs


def cmd_universe(args) -> None:
    ents = universe_mod.load(active_only=args.active)
    if args.country:
        ents = [e for e in ents if e.country == args.country]
    if args.count:
        print(len(ents))
        return
    for e in ents:
        print(f"{e.ticker}\t{e.country}\t{e.name}")


def cmd_fetch(args) -> None:
    fs = _fetch(args.ticker)
    concepts = sorted({f.concept for f in fs.facts})
    print(f"{args.ticker}: {len(fs.facts)} facts, {len(concepts)} concepts")
    for c in concepts[: args.limit]:
        print(" ", c)


def cmd_facts(args) -> None:
    fs = _fetch(args.ticker)
    rows = [f for f in fs.facts if not args.concept or f.concept == args.concept]
    rows.sort(key=lambda f: f.period.end)
    for f in rows[-args.limit :]:
        print(json.dumps({"cid": f.cid, **f.payload()}, default=str))


def cmd_smart(args) -> None:
    fs = _fetch(args.ticker)
    for f in derive_all(fs):
        print(json.dumps({"cid": f.cid, "decimal": str(f.decimal), **f.payload()}, default=str))


def cmd_knit(args) -> None:
    from .knit import HAS_KNITWEB

    if not HAS_KNITWEB:
        print("knitweb (pulse) not installed — pip install from the pulse repo", file=sys.stderr)
        sys.exit(2)
    from knitweb.core import crypto
    from knitweb.fabric.web import Web
    from .knit import FinFieldKnitweb

    fs = _fetch(args.ticker)
    derived = derive_all(fs)
    priv = args.key or crypto.generate_keypair()[0]
    kw = FinFieldKnitweb(priv)
    web = Web()
    result = kw.weave_factset(fs, web, derived=derived)
    print(f"entity {result['entity']}")
    nodes, edges = web.size
    print(f"woven {len(result['facts'])} fact records ({len(derived)} derived): {nodes} nodes, {edges} edges")


def cmd_publish(args) -> None:
    from .publish import Publisher
    from .smart.derive import derive_all

    fs = _fetch(args.ticker)
    pub = Publisher(Path(args.repo))
    n = pub.publish_factset(fs, derived=derive_all(fs))
    head = pub.commit()
    print(f"published {n} new records; feed length {head['length']}, root {head['root'][:20]}…")


def cmd_announce(args) -> None:
    from .publish import Publisher

    print(json.dumps(Publisher(Path(args.repo)).announce()))


def cmd_serve(args) -> None:
    import asyncio

    from .publish import Publisher

    asyncio.run(Publisher(Path(args.repo)).serve())


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="finfield")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn, needs_ticker in (
        ("publish", cmd_publish, True),
        ("announce", cmd_announce, False),
        ("serve", cmd_serve, False),
    ):
        s = sub.add_parser(name, help=f"{name} the feed (P2P)")
        if needs_ticker:
            s.add_argument("ticker")
        s.add_argument("--repo", required=True, help="feed-repo working copy (e.g. a FinField/facts clone)")
        s.set_defaults(fn=fn)

    u = sub.add_parser("universe", help="list the 20k+ company universe")
    u.add_argument("--active", action="store_true")
    u.add_argument("--country")
    u.add_argument("--count", action="store_true")
    u.set_defaults(fn=cmd_universe)

    for name, fn in (("fetch", cmd_fetch), ("facts", cmd_facts), ("smart", cmd_smart), ("knit", cmd_knit)):
        s = sub.add_parser(name)
        s.add_argument("ticker")
        if name in ("fetch", "facts"):
            s.add_argument("--limit", type=int, default=20)
        if name == "facts":
            s.add_argument("--concept")
        if name == "knit":
            s.add_argument("--key", help="publisher private key (hex); generated if omitted")
        s.set_defaults(fn=fn)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
