from pathlib import Path

from app import federation_core as f


def test_hash_and_merkle():
    digest = f.sha256_bytes(b"abc")
    assert f.normalize_sha256("sha256:" + digest) == digest
    assert len(f.merkle_root([digest])) == 64
    assert f.verify_chunk(b"abc", digest)


def test_chunk_math():
    assert f.chunk_count(10, 4) == 3
    assert f.chunk_range(1, 10, 4) == (4, 7)
    assert f.byte_range_chunks(3, 8, 10, 4) == [0, 1, 2]
    assert f.coalesce_indices({0, 1, 3}) == [(0, 1), (3, 3)]
    assert f.expand_ranges([(0, 1), (3, 3)]) == {0, 1, 3}


def test_bitmap_and_resume_state():
    encoded = f.bitmap_encode({0, 2, 7}, 8)
    assert f.bitmap_decode(encoded, 8) == {0, 2, 7}
    state = f.transfer_state("0" * 64, 8, {0, 2, 7})
    assert f.transfer_state_have(state) == {0, 2, 7}
    assert f.transfer_pause(state)["status"] == "paused"
    assert f.transfer_resume(state)["status"] == "active"


def test_scheduler_prefers_rarest_and_healthier_source():
    sources = {"b": {0, 1}, "c": {1, 2}}
    assert f.rarest_first(set(), sources, 3)[0] in {0, 2}
    plan = f.scheduler_plan(set(), sources, {"b": 1, "c": 10}, 3, 2)
    assert len(plan) == 2
    assert all(source in sources for _, source in plan)


def test_policy_is_default_deny():
    assert not f.default_deny_policy().may_receive("documents")
    assert f.backup_policy().may_receive("documents")
    assert not f.backup_policy().may_send("documents")
    assert f.collector_policy("contacts").may_receive("contacts")


def test_capability_is_scoped_and_expires():
    secret = b"secret"
    payload = f.capability_payload("t", "b", "c", "a" * 64, 200, 100)
    sig = f.sign_capability(payload, secret)
    assert f.verify_capability(payload, sig, secret, now=100)
    assert not f.verify_capability(payload, sig, secret, now=201)
    assert f.capability_allows_bytes(payload, 100)
    assert not f.capability_allows_bytes(payload, 101)


def test_safe_transfer_urls():
    assert f.safe_https_url("https://example.com/federation")
    assert not f.safe_https_url("http://example.com/federation")
    assert not f.safe_https_url("https://user:pw@example.com/federation")
    assert f.redirect_allowed("https://example.com/a", "https://example.com/b")
    assert not f.redirect_allowed("https://example.com/a", "https://other.example/b")


def test_manifest_roundtrip(tmp_path: Path):
    path = tmp_path / "blob.bin"; path.write_bytes(b"abcdefghij")
    manifest = f.build_manifest(path, 4)
    assert manifest["chunk_count"] == 3
    assert f.manifest_valid(manifest)
    assert f.verify_file(path, manifest["blob_hash"])


def test_delta_sync_helpers():
    entries = [{"sequence": 2, "origin_instance_id": "a", "object_id": "x", "revision": 1}, {"sequence": 3, "origin_instance_id": "a", "object_id": "x", "revision": 2}]
    assert f.cursor_advance(1, entries) == 3
    assert len(f.dedupe_changes(entries)) == 1
    assert f.page_changes(entries, 2, 10)[0]["sequence"] == 3


def test_replication_and_transfer_jobs():
    assert f.replication_needed(1, 3) == 2
    assert f.choose_replication_targets(["b", "c", "d"], {"b"}, 3) == ["c", "d"]
    assert f.copy_job("a" * 64, "b", "c")["operation"] == "COPY"
    assert f.repair_job("a" * 64, ["b"], "c")["operation"] == "REPAIR"


def test_limits_retries_and_progress():
    assert f.bounded_parallelism(99) == 16
    assert f.bounded_page_size(5000) == 1000
    assert f.retryable_status(503)
    assert f.terminal_status(404)
    assert f.transfer_progress(50, 100) == .5
    assert f.transfer_eta(100, 20) == 5


def test_filters_and_conflicts():
    policy = {"max_size": 10, "mime_types": ["text/plain"], "tags": ["sync"]}
    assert f.transfer_policy_allows(5, "text/plain", {"sync"}, policy)
    assert not f.transfer_policy_allows(11, "text/plain", {"sync"}, policy)
    assert f.conflict_required("local", "remote", True)
    assert f.quarantine_reason(False, True, True) == "hash_mismatch"


def test_negotiation_and_source_advertisement():
    assert f.compatible_version([1, 2], [1]) == 1
    ad = f.source_advertisement("peer-b", "a" * 64, {0, 2}, 3)
    assert f.parse_source_advertisement(ad) == {0, 2}
    assert f.capability_summary()["multi_source"] is True
