import inspect
import unittest
from pathlib import Path

import app.v2.contracts as contracts


ROOT = Path(__file__).resolve().parents[1]


class V2ArchitectureContractTest(unittest.TestCase):
    def test_contract_module_has_no_framework_or_v1_storage_dependencies(self):
        source = inspect.getsource(contracts)
        forbidden = (
            "import flask",
            "from flask",
            "app.document_store",
            "from ..document_store",
            "app.federation_",
            "from ..federation_",
        )
        for token in forbidden:
            self.assertNotIn(token, source)

    def test_result_requires_exactly_one_value_or_error(self):
        ok = contracts.OperationResult.success("value")
        self.assertTrue(ok.ok)
        self.assertEqual("value", ok.value)
        failed = contracts.OperationResult.failure(contracts.ErrorCode.CONFLICT, "conflict")
        self.assertFalse(failed.ok)
        self.assertEqual(contracts.ErrorCode.CONFLICT, failed.error.code)
        with self.assertRaises(ValueError):
            contracts.OperationResult()
        with self.assertRaises(ValueError):
            contracts.OperationResult(value="value", error=contracts.OperationError(contracts.ErrorCode.INTERNAL_ERROR, "x"))

    def test_logical_and_physical_ids_are_distinct_types(self):
        logical = contracts.LogicalObjectId("document-1")
        physical = contracts.PhysicalBlobId("blob-1")
        self.assertNotEqual(logical, physical)
        self.assertEqual("document-1", logical.value)
        self.assertEqual("blob-1", physical.value)

    def test_job_terminal_states_are_explicit(self):
        self.assertFalse(contracts.JobState.QUEUED.terminal)
        self.assertFalse(contracts.JobState.RUNNING.terminal)
        self.assertTrue(contracts.JobState.SUCCEEDED.terminal)
        self.assertTrue(contracts.JobState.FAILED.terminal)
        self.assertTrue(contracts.JobState.CANCELLED.terminal)

    def test_architecture_document_covers_required_contracts(self):
        text = (ROOT / "docs" / "ARCHITECTURE_V2.md").read_text(encoding="utf-8")
        for heading in (
            "## Layer boundaries",
            "## Allowed dependencies",
            "## Internal interfaces",
            "## Error/result model",
            "## Transaction boundaries",
            "## Event and job model",
            "## Persistent format versioning",
            "## Migration rules",
            "## Deprecation rules",
        ):
            self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main()
