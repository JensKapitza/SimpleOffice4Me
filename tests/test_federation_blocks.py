import random
import tempfile
import unittest
from pathlib import Path

from app.federation_blocks import (
    FederationBlockStore,
    build_content_manifest,
    content_manifest_valid,
    missing_block_hashes,
    reusable_bytes,
)


class FederationBlocksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_content_defined_blocks_resynchronize_after_insertion(self):
        rng = random.Random(42)
        original = bytes(rng.randrange(0, 256) for _ in range(16 * 1024))
        changed = original[:3000] + (b"INSERTED-DATA-" * 17) + original[3000:]
        a = self.root / "a.bin"
        b = self.root / "b.bin"
        a.write_bytes(original)
        b.write_bytes(changed)
        manifest_a = build_content_manifest(a, min_size=64, avg_size=128, max_size=256)
        manifest_b = build_content_manifest(b, min_size=64, avg_size=128, max_size=256)
        self.assertTrue(content_manifest_valid(manifest_a))
        self.assertTrue(content_manifest_valid(manifest_b))
        hashes_a = {block["sha512"] for block in manifest_a["blocks"]}
        hashes_b = {block["sha512"] for block in manifest_b["blocks"]}
        common = hashes_a & hashes_b
        self.assertGreater(len(common), 10)
        self.assertGreater(reusable_bytes(manifest_b, hashes_a), len(changed) // 2)
        self.assertLess(len(missing_block_hashes(manifest_b, hashes_a)), len(manifest_b["blocks"]))

    def test_block_store_reuses_bytes_from_other_file(self):
        source = self.root / "source.bin"
        source.write_bytes((b"abcdefgh12345678" * 1024) + (b"tail" * 100))
        store = FederationBlockStore(self.root)
        manifest = build_content_manifest(source, min_size=64, avg_size=128, max_size=256)
        store.register_manifest(source, manifest)
        available = store.available([block["sha512"] for block in manifest["blocks"]])
        self.assertEqual(len(available), len({block["sha512"] for block in manifest["blocks"]}))
        target = self.root / "reconstructed.bin"
        result = store.reconstruct(target, manifest)
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(result["bytes"], len(source.read_bytes()))

    def test_cached_block_rejects_wrong_sha512(self):
        store = FederationBlockStore(self.root)
        with self.assertRaises(ValueError):
            store.put_cached_block("a" * 128, b"wrong")


if __name__ == "__main__":
    unittest.main()
