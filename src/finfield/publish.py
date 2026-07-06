"""P2P publishing: a signed, sharded feed of finfact records — no local store.

FinField data never lives on the ingest machine. The feed (append-only
shards + signed head) lives in the ``FinField/facts`` git repo, which the
field infrastructure (finfield.github.io, 5mart.ml's git-pull deploy)
serves over HTTPS; any knitweb node can bootstrap the feed from there,
verify the head signature and every record CID, and replicate it onward
with feed-request/feed-data anti-entropy. The ingest machine only holds a
transient working copy that is deleted after push.

Layout of a feed repo working copy:

    feed/records-00001.jsonl     append-only shards, SHARD_SIZE records each
    feed/head.json               signed FeedHead over the whole history
    feed/MANIFEST.json           shard list + counts, for HTTP bootstrap

Only the publisher key (``~/.finfield/publisher.key``, a credential, not
data) persists locally.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

from .knit import HAS_KNITWEB, FinFieldKnitweb
from .model import FactSet

DEFAULT_KEY = Path(os.environ.get("FINFIELD_KEY", Path.home() / ".finfield/publisher.key"))
DEFAULT_RELAY = "https://5mart.ml"
SHARD_SIZE = 20_000


class FeedStore:
    """Sharded append-only record log inside a feed-repo working copy."""

    def __init__(self, repo_dir: Path):
        self.dir = Path(repo_dir) / "feed"
        self.dir.mkdir(parents=True, exist_ok=True)

    def shards(self) -> list[Path]:
        return sorted(self.dir.glob("records-*.jsonl"))

    def iter_records(self) -> Iterable[dict]:
        for shard in self.shards():
            with shard.open() as f:
                for line in f:
                    if line.strip():
                        yield json.loads(line)

    def count(self) -> int:
        return sum(1 for _ in self.iter_records())

    def known_cids(self) -> set:
        from knitweb.core import canonical

        return {canonical.cid(rec) for rec in self.iter_records()}

    def append(self, records: list[dict]) -> int:
        """Append records, filling the last shard up to SHARD_SIZE."""
        shards = self.shards()
        if shards:
            last = shards[-1]
            n_last = sum(1 for line in last.open() if line.strip())
            idx = int(last.stem.split("-")[1])
        else:
            last, n_last, idx = None, SHARD_SIZE, 0
        written = 0
        buf = []
        for rec in records:
            if n_last >= SHARD_SIZE:
                if buf and last is not None:
                    with last.open("a") as f:
                        f.writelines(buf)
                    buf = []
                idx += 1
                last = self.dir / f"records-{idx:05d}.jsonl"
                last.touch()
                n_last = 0
            buf.append(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
            n_last += 1
            written += 1
        if buf and last is not None:
            with last.open("a") as f:
                f.writelines(buf)
        return written


class Publisher:
    def __init__(self, repo_dir: Path, key_path: Optional[Path] = None):
        if not HAS_KNITWEB:
            raise ImportError("knitweb (pulse) is required for P2P publishing")
        from knitweb.core import crypto

        kp = Path(key_path) if key_path else DEFAULT_KEY
        if kp.exists():
            priv = kp.read_text().strip()
        else:
            priv = crypto.generate_keypair()[0]
            kp.parent.mkdir(parents=True, exist_ok=True)
            kp.touch(mode=0o600)
            kp.write_text(priv)
        self._priv = priv
        self.kw = FinFieldKnitweb(priv)
        self.store = FeedStore(repo_dir)

    # -- publish -------------------------------------------------------------
    def publish_factset(self, fs: FactSet, derived: Optional[list] = None) -> int:
        """Weave + attest a factset, append records not yet in the feed.

        Idempotent per CID: re-publishing after a new filing appends only
        the delta — this is the P2P update path.
        """
        from knitweb.fabric.web import Web

        result = self.kw.weave_factset(fs, Web(), derived=derived)
        return self.publish_records([att.record for att in result["attestations"]])

    def publish_records(self, records: list[dict], known: Optional[set] = None) -> int:
        from knitweb.core import canonical

        known = self.store.known_cids() if known is None else known
        fresh = []
        for rec in records:
            c = canonical.cid(rec)
            if c not in known:
                fresh.append(rec)
                known.add(c)
        return self.store.append(fresh)

    # -- head / manifest ---------------------------------------------------------
    def feed(self):
        """Rebuild the signed Feed from the shards (deterministic order)."""
        from knitweb.fabric.feed import Feed

        f = Feed(self._priv)
        for rec in self.store.iter_records():
            f.append(rec)
        return f

    def commit(self) -> dict:
        """Sign a head over the current history and write head + manifest."""
        h = self.feed().head()
        head = {"feed": h.feed, "root": h.root, "length": h.length, "fork": h.fork, "sig": h.sig}
        (self.store.dir / "head.json").write_text(json.dumps(head, indent=1))
        manifest = {
            "publisher": self.kw.address,
            "shards": [p.name for p in self.store.shards()],
            "records": h.length,
        }
        (self.store.dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
        return head

    # -- announce / serve ----------------------------------------------------------
    def announce(self, relay_url: str = DEFAULT_RELAY, mailbox: str = "finfield") -> dict:
        """Drop the signed head in a relay mailbox so live nodes learn of it."""
        import base64
        import urllib.request

        head = json.loads((self.store.dir / "head.json").read_text())
        frame = base64.b64encode(json.dumps({"kind": "finfield-head", **head}).encode()).decode()
        req = urllib.request.Request(
            f"{relay_url}/api/relay/send",
            data=json.dumps({"mailbox": mailbox, "rid": head["length"], "frame": frame}).encode(),
            headers={"Content-Type": "application/json"},
        )
        from .sources.sec_edgar import _ssl_context

        with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as resp:
            return {"status": resp.status, "head": head["root"][:16], "length": head["length"]}

    async def serve(self, relay_url: str = DEFAULT_RELAY, host: str = "127.0.0.1", port: int = 0):
        """Run a P2P node offering the feed for replication (Ctrl-C to stop)."""
        import asyncio

        from knitweb.p2p.node import AsyncioP2PNode
        from knitweb.p2p.relay import RelayTransport

        relay = RelayTransport(base_url=relay_url, mailbox=self.kw.address)
        node = AsyncioP2PNode(host=host, port=port, extra_transports=[relay])
        node.add_feed(self.feed())
        await node.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await node.stop()
