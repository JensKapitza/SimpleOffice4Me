import hashlib
import unittest

from app.v2.contracts import LogicalObjectId, PhysicalBlobId
from app.v2.fragments import (
    FragmentDescriptor,
    FragmentPlan,
    FragmentState,
    RecoverySet,
    assess_fragments,
)


class FragmentRecoveryContractTest(unittest.TestCase):
    def _set(self):
        payloads = [b"one", b"two", b"three", b"four", b"five", b"six"]
        descriptors = tuple(
            FragmentDescriptor(
                index=index,
                physical_id=PhysicalBlobId(f"fragment-{index}"),
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            for index, payload in enumerate(payloads)
        )
        recovery = RecoverySet(
            schema="simpleoffice-v2-fragments/v1",
            recovery_id="recovery-1",
            object_id=LogicalObjectId("document-1"),
            version_id="version-1",
            plan=FragmentPlan(k=4, n=6, codec="external-test-codec", codec_version="1"),
            original_size=123,
            padding=0,
            content_sha256=hashlib.sha256(b"synthetic").hexdigest(),
            fragments=descriptors,
        )
        return recovery, payloads

    def test_assessment_distinguishes_valid_corrupt_and_missing(self):
        recovery, payloads = self._set()
        available = {
            "fragment-0": payloads[0],
            "fragment-1": payloads[1],
            "fragment-2": payloads[2],
            "fragment-3": payloads[3],
            "fragment-4": b"corrupt",
        }
        result = assess_fragments(recovery, available)
        self.assertTrue(result.recoverable)
        self.assertEqual(4, result.valid_count)
        self.assertEqual(1, result.corrupt_count)
        self.assertEqual(1, result.missing_count)
        self.assertEqual(FragmentState.PRESENT_CORRUPT, result.states[4])
        self.assertEqual(FragmentState.MISSING, result.states[5])

    def test_recovery_requires_k_valid_fragments_not_merely_present_fragments(self):
        recovery, payloads = self._set()
        available = {
            "fragment-0": payloads[0],
            "fragment-1": payloads[1],
            "fragment-2": payloads[2],
            "fragment-3": b"corrupt",
            "fragment-4": b"corrupt",
            "fragment-5": b"corrupt",
        }
        result = assess_fragments(recovery, available)
        self.assertFalse(result.recoverable)
        self.assertEqual(3, result.valid_count)

    def test_plan_and_descriptor_shape_are_fail_closed(self):
        with self.assertRaises(ValueError):
            FragmentPlan(k=5, n=4, codec="x", codec_version="1")
        recovery, _ = self._set()
        with self.assertRaises(ValueError):
            RecoverySet(
                schema=recovery.schema,
                recovery_id=recovery.recovery_id,
                object_id=recovery.object_id,
                version_id=recovery.version_id,
                plan=recovery.plan,
                original_size=recovery.original_size,
                padding=recovery.padding,
                content_sha256=recovery.content_sha256,
                fragments=recovery.fragments[:-1],
            )


if __name__ == "__main__":
    unittest.main()
