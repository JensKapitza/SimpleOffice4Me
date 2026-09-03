import tempfile
import unittest
from pathlib import Path

from app import federation_core as f


class FederationCoreTest(unittest.TestCase):
    def test_hash_and_merkle(self):
        digest = f.sha256_bytes(b"abc")
        self.assertEqual(f.normalize_sha256("sha256:" + digest), digest)
        self.assertEqual(len(f.merkle_root([digest])), 64)
        self.assertTrue(f.verify_chunk(b"abc", digest))

    def test_chunk_math(self):
        self.assertEqual(f.chunk_count(10, 4), 3)
        self.assertEqual(f.chunk_range(1, 10, 4), (4, 7))
        self.assertEqual(f.byte_range_chunks(3, 8, 10, 4), [0, 1, 2])
        self.assertEqual(f.coalesce_indices({0, 1, 3}), [(0, 1), (3, 3)])
        self.assertEqual(f.expand_ranges([(0, 1), (3, 3)]), {0, 1, 3})

    def test_bitmap_and_resume_state(self):
        encoded = f.bitmap_encode({0, 2, 7}, 8)
        self.assertEqual(f.bitmap_decode(encoded, 8), {0, 2, 7})
        state = f.transfer_state("0" * 64, 8, {0, 2, 7})
        self.assertEqual(f.transfer_state_have(state), {0, 2, 7})
        self.assertEqual(f.transfer_pause(state)["status"], "paused")
        self.assertEqual(f.transfer_resume(state)["status"], "active")

    def test_scheduler_prefers_rarest_and_healthier_source(self):
        sources = {"b": {0, 1}, "c": {1, 2}}
        self.assertIn(f.rarest_first(set(), sources, 3)[0], {0, 2})
        plan = f.scheduler_plan(set(), sources, {"b": 1, "c": 10}, 3, 2)
        self.assertEqual(len(plan), 2)
        self.assertTrue(all(source in sources for _, source in plan))

    def test_policy_is_default_deny(self):
        self.assertFalse(f.default_deny_policy().may_receive("documents"))
        self.assertTrue(f.backup_policy().may_receive("documents"))
        self.assertFalse(f.backup_policy().may_send("documents"))
        self.assertTrue(f.collector_policy("contacts").may_receive("contacts"))

    def test_capability_is_scoped_and_expires(self):
        secret = b"secret"
        payload = f.capability_payload("t", "b", "c", "a" * 64, 200, 100)
        sig = f.sign_capability(payload, secret)
        self.assertTrue(f.verify_capability(payload, sig, secret, now=100))
        self.assertFalse(f.verify_capability(payload, sig, secret, now=201))
        self.assertTrue(f.capability_allows_bytes(payload, 100))
        self.assertFalse(f.capability_allows_bytes(payload, 101))

    def test_safe_transfer_urls(self):
        self.assertTrue(f.safe_https_url("https://example.com/federation"))
        self.assertFalse(f.safe_https_url("http://example.com/federation"))
        self.assertFalse(f.safe_https_url("https://user:pw@example.com/federation"))
        self.assertTrue(f.redirect_allowed("https://example.com/a", "https://example.com/b"))
        self.assertFalse(f.redirect_allowed("https://example.com/a", "https://other.example/b"))

    def test_manifest_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "blob.bin"
            path.write_bytes(b"abcdefghij")
            manifest = f.build_manifest(path, 4)
            self.assertEqual(manifest["chunk_count"], 3)
            self.assertTrue(f.manifest_valid(manifest))
            self.assertTrue(f.verify_file(path, manifest["blob_hash"]))

    def test_delta_sync_helpers(self):
        entries = [
            {"sequence": 2, "origin_instance_id": "a", "object_id": "x", "revision": 1},
            {"sequence": 3, "origin_instance_id": "a", "object_id": "x", "revision": 2},
        ]
        self.assertEqual(f.cursor_advance(1, entries), 3)
        self.assertEqual(len(f.dedupe_changes(entries)), 1)
        self.assertEqual(f.page_changes(entries, 2, 10)[0]["sequence"], 3)

    def test_replication_and_transfer_jobs(self):
        self.assertEqual(f.replication_needed(1, 3), 2)
        self.assertEqual(f.choose_replication_targets(["b", "c", "d"], {"b"}, 3), ["c", "d"])
        self.assertEqual(f.copy_job("a" * 64, "b", "c")["operation"], "COPY")
        self.assertEqual(f.repair_job("a" * 64, ["b"], "c")["operation"], "REPAIR")

    def test_limits_retries_and_progress(self):
        self.assertEqual(f.bounded_parallelism(99), 16)
        self.assertEqual(f.bounded_page_size(5000), 1000)
        self.assertTrue(f.retryable_status(503))
        self.assertTrue(f.terminal_status(404))
        self.assertEqual(f.transfer_progress(50, 100), .5)
        self.assertEqual(f.transfer_eta(100, 20), 5)

    def test_filters_and_conflicts(self):
        policy = {"max_size": 10, "mime_types": ["text/plain"], "tags": ["sync"]}
        self.assertTrue(f.transfer_policy_allows(5, "text/plain", {"sync"}, policy))
        self.assertFalse(f.transfer_policy_allows(11, "text/plain", {"sync"}, policy))
        self.assertTrue(f.conflict_required("local", "remote", True))
        self.assertEqual(f.quarantine_reason(False, True, True), "hash_mismatch")

    def test_negotiation_and_source_advertisement(self):
        self.assertEqual(f.compatible_version([1, 2], [1]), 1)
        ad = f.source_advertisement("peer-b", "a" * 64, {0, 2}, 3)
        self.assertEqual(f.parse_source_advertisement(ad), {0, 2})
        self.assertTrue(f.capability_summary()["multi_source"])


if __name__ == "__main__":
    unittest.main()
