"""Publish-layer tests: sharded feed store, idempotent publish, signed head."""
import json

import pytest

from finfield.knit import HAS_KNITWEB
from finfield.publish import FeedStore, Publisher, SHARD_SIZE
from tests.test_derive import _fs

requires_knitweb = pytest.mark.skipif(not HAS_KNITWEB, reason="knitweb (pulse) not installed")


# 1 — shards fill up to SHARD_SIZE and roll over
def test_shard_rollover(tmp_path, monkeypatch):
    monkeypatch.setattr("finfield.publish.SHARD_SIZE", 3)
    store = FeedStore(tmp_path)
    store.append([{"i": n} for n in range(7)])
    assert [p.name for p in store.shards()] == [
        "records-00001.jsonl",
        "records-00002.jsonl",
        "records-00003.jsonl",
    ]
    assert [r["i"] for r in store.iter_records()] == list(range(7))
    store.append([{"i": 7}])  # fills the last shard, no new file
    assert len(store.shards()) == 3 and store.count() == 8


@requires_knitweb
def test_publish_idempotent(tmp_path):
    key = tmp_path / "publisher.key"
    pub = Publisher(tmp_path / "repo", key_path=key)
    fs = _fs([100, 200, 300, 400, 500])
    n1 = pub.publish_factset(fs)
    n2 = pub.publish_factset(fs)  # re-publish: no new records
    assert n1 > 0 and n2 == 0
    fs2 = _fs([100, 200, 300, 400, 500, 600][1:])  # shifted data -> delta only
    assert 0 < pub.publish_factset(fs2) < n1


@requires_knitweb
def test_head_signed_and_deterministic(tmp_path):
    key = tmp_path / "publisher.key"
    pub = Publisher(tmp_path / "repo", key_path=key)
    pub.publish_factset(_fs([100, 200, 300, 400, 500]))
    h1 = pub.commit()
    h2 = Publisher(tmp_path / "repo", key_path=key).commit()  # rebuild from disk
    # ECDSA sigs are nonce-randomized; the committed state is deterministic
    for k in ("feed", "root", "length", "fork"):
        assert h1[k] == h2[k]
    assert h1["length"] == pub.store.count()
    manifest = json.loads((pub.store.dir / "MANIFEST.json").read_text())
    assert manifest["records"] == h1["length"]
    assert manifest["publisher"] == pub.kw.address

    # the head signature verifies
    from knitweb.fabric.feed import verify_head

    assert verify_head(pub.feed().head())


@requires_knitweb
def test_key_persist(tmp_path):
    key = tmp_path / "publisher.key"
    a1 = Publisher(tmp_path / "r1", key_path=key).kw.address
    a2 = Publisher(tmp_path / "r2", key_path=key).kw.address
    assert a1 == a2
