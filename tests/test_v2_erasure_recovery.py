import hashlib
import unittest
from unittest.mock import patch

from app.v2.adapters.zfec_codec import ZfecErasureCodec, codec_for_plan
from app.v2.contracts import LogicalObjectId, PhysicalBlobId
from app.v2.fragments import (
    FragmentDescriptor,
    FragmentPlan,
    FragmentRecoveryError,
    RecoverySet,
    encode_recovery_set,
    recover_payload,
    recovery_set_from_dict,
    recovery_set_to_dict,
)


class ReplicatedTestCodec:
    name = "test-replica"
    version = "1"

    def encode(self, payload, plan):
        return [bytes(payload) for _ in range(plan.n)]

    def decode(self, fragments, plan, *, original_size, padding):
        return next(iter(fragments.values()))[:original_size]


class BadDecodeCodec(ReplicatedTestCodec):
    def decode(self, fragments, plan, *, original_size, padding):
        return b"x" * original_size


class ErasureRecoveryPipelineTest(unittest.TestCase):
    def setUp(self):
        self.plan = FragmentPlan(k=2, n=3, codec="test-replica", codec_version="1")
        self.codec = ReplicatedTestCodec()
        self.payload = b"synthetic recovery payload"

    def test_encode_assess_and_recover_with_missing_fragment(self):
        encoded = encode_recovery_set(
            self.payload,
            object_id=LogicalObjectId("document-1"),
            version_id="version-1",
            plan=self.plan,
            codec=self.codec,
            recovery_id="recovery-1",
        )
        recovery = encoded.recovery_set
        self.assertEqual(3, len(recovery.fragments))
        self.assertEqual(3, len(set(item.physical_id.value for item in recovery.fragments)))
        self.assertEqual(hashlib.sha256(self.payload).hexdigest(), recovery.content_sha256)

        available = dict(encoded.fragments)
        del available[recovery.fragments[1].physical_id.value]
        self.assertEqual(self.payload, recover_payload(recovery, available, self.codec))

    def test_corrupt_fragment_does_not_count_towards_k(self):
        encoded = encode_recovery_set(
            self.payload,
            object_id=LogicalObjectId("document-1"),
            version_id="version-1",
            plan=self.plan,
            codec=self.codec,
        )
        recovery = encoded.recovery_set
        available = {
            recovery.fragments[0].physical_id.value: encoded.fragments[recovery.fragments[0].physical_id.value],
            recovery.fragments[1].physical_id.value: b"corrupt",
        }
        with self.assertRaisesRegex(FragmentRecoveryError, "requires 2 valid fragments"):
            recover_payload(recovery, available, self.codec)

    def test_reconstructed_whole_content_digest_is_verified(self):
        encoded = encode_recovery_set(
            self.payload,
            object_id=LogicalObjectId("document-1"),
            version_id="version-1",
            plan=self.plan,
            codec=self.codec,
        )
        with self.assertRaisesRegex(FragmentRecoveryError, "integrity mismatch"):
            recover_payload(encoded.recovery_set, encoded.fragments, BadDecodeCodec())

    def test_recovery_set_round_trip_is_portable_and_fail_closed(self):
        encoded = encode_recovery_set(
            self.payload,
            object_id=LogicalObjectId("document-1"),
            version_id="version-1",
            plan=self.plan,
            codec=self.codec,
        )
        restored = recovery_set_from_dict(recovery_set_to_dict(encoded.recovery_set))
        self.assertEqual(encoded.recovery_set, restored)

        document = recovery_set_to_dict(encoded.recovery_set)
        document["schema"] = "unknown/v9"
        with self.assertRaises(ValueError):
            recovery_set_from_dict(document)

    def test_duplicate_physical_fragment_ids_are_rejected(self):
        digest = hashlib.sha256(b"one").hexdigest()
        duplicate = PhysicalBlobId("same-physical-fragment")
        with self.assertRaisesRegex(ValueError, "physical ids must be unique"):
            RecoverySet(
                schema="simpleoffice-v2-fragments/v1",
                recovery_id="recovery-1",
                object_id=LogicalObjectId("document-1"),
                version_id="version-1",
                plan=FragmentPlan(k=1, n=2, codec="x", codec_version="1"),
                original_size=3,
                padding=0,
                content_sha256=digest,
                fragments=(
                    FragmentDescriptor(0, duplicate, 3, digest),
                    FragmentDescriptor(1, duplicate, 3, digest),
                ),
            )

    def test_codec_contract_must_match_recovery_metadata(self):
        mismatched = FragmentPlan(k=2, n=3, codec="other", codec_version="1")
        with self.assertRaisesRegex(FragmentRecoveryError, "codec mismatch"):
            encode_recovery_set(
                self.payload,
                object_id=LogicalObjectId("document-1"),
                version_id="version-1",
                plan=mismatched,
                codec=self.codec,
            )


class ZfecAdapterTest(unittest.TestCase):
    def test_adapter_wires_share_numbers_and_padding_to_zfec_backend(self):
        calls = {}

        class FakeEncoder:
            def __init__(self, k, n):
                calls["encoder"] = (k, n)

            def encode(self, payload):
                calls["encoded_payload"] = payload
                return [b"abc", b"def", b"ghi"]

        class FakeDecoder:
            def __init__(self, k, n):
                calls["decoder"] = (k, n)

            def decode(self, blocks, share_numbers, padding):
                calls["decode_args"] = (blocks, share_numbers, padding)
                return b"abcdef"

        plan = FragmentPlan(k=2, n=3, codec="zfec", codec_version="1")
        codec = ZfecErasureCodec()
        with patch("app.v2.adapters.zfec_codec._load_backend", return_value=(FakeEncoder, FakeDecoder)):
            shards = codec.encode(b"abcdef", plan)
            decoded = codec.decode({0: shards[0], 2: shards[2]}, plan, original_size=6, padding=0)

        self.assertEqual((2, 3), calls["encoder"])
        self.assertEqual((2, 3), calls["decoder"])
        self.assertEqual(([b"abc", b"ghi"], [0, 2], 0), calls["decode_args"])
        self.assertEqual(b"abcdef", decoded)

    def test_codec_registry_fails_closed_for_unknown_codec(self):
        self.assertIsInstance(
            codec_for_plan(FragmentPlan(k=2, n=3, codec="zfec", codec_version="1")),
            ZfecErasureCodec,
        )
        with self.assertRaisesRegex(FragmentRecoveryError, "unsupported erasure codec"):
            codec_for_plan(FragmentPlan(k=2, n=3, codec="unknown", codec_version="1"))


if __name__ == "__main__":
    unittest.main()
