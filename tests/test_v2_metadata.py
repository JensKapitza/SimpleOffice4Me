import unittest

from app.v2.contracts import LogicalObjectId
from app.v2.metadata import (
    FilenameAlias,
    MetadataEnvelope,
    MetadataSource,
    MetadataValue,
    NamespaceRules,
    Provenance,
    TrustLevel,
    UnicodeNormalization,
    VerificationStatus,
    original_value,
)


class V2MetadataTest(unittest.TestCase):
    def provenance(self, source):
        return Provenance(source=source, source_ref="synthetic-source", observed_at="2026-09-20T00:00:00Z")

    def test_original_metadata_is_immutable_and_channel_checked(self):
        original = original_value(
            "mime_type",
            "application/pdf",
            self.provenance(MetadataSource.ORIGINAL),
            verification=VerificationStatus.VERIFIED,
        )
        envelope = MetadataEnvelope(object_id=LogicalObjectId("object-1"), original=(original,))
        self.assertTrue(envelope.original[0].immutable)

        with self.assertRaises(ValueError):
            MetadataValue(
                name="mime_type",
                value="text/plain",
                provenance=self.provenance(MetadataSource.OBSERVED),
                immutable=True,
            )

    def test_observed_imported_and_federated_values_remain_distinct(self):
        observed = MetadataValue(
            "title", "Observed", self.provenance(MetadataSource.OBSERVED), trust=TrustLevel.LOCAL
        )
        imported = MetadataValue(
            "title", "Imported", self.provenance(MetadataSource.IMPORTED), trust=TrustLevel.UNTRUSTED
        )
        federated = MetadataValue(
            "title", "Federated", self.provenance(MetadataSource.FEDERATED), trust=TrustLevel.UNKNOWN
        )
        envelope = MetadataEnvelope(
            object_id=LogicalObjectId("object-2"),
            observed=(observed,),
            imported=(imported,),
            federated=(federated,),
        )
        self.assertEqual((observed, imported, federated), envelope.values_for("title"))

    def test_filename_alias_is_not_object_identity(self):
        alias = FilenameAlias(
            "Report.PDF",
            self.provenance(MetadataSource.OBSERVED),
            NamespaceRules(case_sensitive=False),
        )
        object_id = LogicalObjectId("Report.PDF")
        self.assertNotEqual(alias, object_id)
        self.assertEqual("report.pdf", alias.collision_key)

    def test_unicode_and_case_rules_detect_alias_collisions(self):
        rules = NamespaceRules(
            case_sensitive=False,
            unicode_normalization=UnicodeNormalization.NFC,
            max_name_bytes=255,
        )
        first = FilenameAlias("Café.txt", self.provenance(MetadataSource.OBSERVED), rules)
        second = FilenameAlias("CAFÉ.TXT", self.provenance(MetadataSource.IMPORTED), rules)
        envelope = MetadataEnvelope(object_id=LogicalObjectId("object-3"), aliases=(first, second))
        self.assertEqual(1, len(envelope.alias_collisions()))

    def test_namespace_rejects_path_components_and_length_overflow(self):
        rules = NamespaceRules(max_name_bytes=8)
        with self.assertRaises(ValueError):
            rules.normalize("../bad")
        with self.assertRaises(ValueError):
            rules.normalize("directory/file")
        with self.assertRaises(ValueError):
            rules.normalize("0123456789")


if __name__ == "__main__":
    unittest.main()
