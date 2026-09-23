import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from app.v2.contracts import LogicalObjectId
from app.v2.fragments import FragmentPlan, encode_recovery_set, recovery_set_to_dict
from app.v2.recovery_cli import main


class ReplicatedCliCodec:
    name = "test-replica"
    version = "1"

    def encode(self, payload, plan):
        return [bytes(payload) for _ in range(plan.n)]

    def decode(self, fragments, plan, *, original_size, padding):
        return next(iter(fragments.values()))[:original_size]


class FragmentRecoveryCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.payload = b"portable fragment recovery payload"
        self.codec = ReplicatedCliCodec()
        self.encoded = encode_recovery_set(
            self.payload,
            object_id=LogicalObjectId("portable-document"),
            version_id="version-1",
            plan=FragmentPlan(k=2, n=3, codec=self.codec.name, codec_version=self.codec.version),
            codec=self.codec,
            recovery_id="portable-recovery",
        )
        self.descriptor = self.root / "recovery.json"
        self.descriptor.write_text(
            json.dumps(recovery_set_to_dict(self.encoded.recovery_set)),
            encoding="utf-8",
        )
        self.fragments = []
        for descriptor in self.encoded.recovery_set.fragments:
            path = self.root / f"fragment-{descriptor.index}.bin"
            path.write_bytes(self.encoded.fragments[descriptor.physical_id.value])
            self.fragments.append(path)

    def tearDown(self):
        self.temp.cleanup()

    def _fragment_args(self, indexes):
        values = []
        for index in indexes:
            values.extend(["--fragment", f"{index}={self.fragments[index]}"])
        return values

    def test_fragment_assessment_does_not_require_application_root(self):
        output = StringIO()
        argv = [
            "fragment-assess",
            "--descriptor",
            str(self.descriptor),
            *self._fragment_args([0, 2]),
        ]
        with redirect_stdout(output):
            code = main(argv)
        report = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertTrue(report["recoverable"])
        self.assertEqual(2, report["valid_count"])
        self.assertEqual("missing", report["states"]["1"])

    def test_corrupt_fragment_is_reported_and_does_not_count(self):
        self.fragments[1].write_bytes(b"corrupt")
        output = StringIO()
        argv = [
            "fragment-assess",
            "--descriptor",
            str(self.descriptor),
            *self._fragment_args([0, 1]),
        ]
        with redirect_stdout(output):
            code = main(argv)
        report = json.loads(output.getvalue())
        self.assertEqual(2, code)
        self.assertFalse(report["recoverable"])
        self.assertEqual(1, report["corrupt_count"])

    def test_fragment_recovery_requires_explicit_apply(self):
        target = self.root / "recovered.bin"
        output = StringIO()
        argv = [
            "fragment-recover",
            "--descriptor",
            str(self.descriptor),
            *self._fragment_args([0, 2]),
            "--output",
            str(target),
        ]
        with redirect_stdout(output):
            code = main(argv)
        self.assertEqual(3, code)
        self.assertFalse(target.exists())
        self.assertIn("read-only mode", output.getvalue())

    def test_fragment_recovery_verifies_then_exports_atomically(self):
        target = self.root / "recovered.bin"
        argv = [
            "fragment-recover",
            "--descriptor",
            str(self.descriptor),
            *self._fragment_args([0, 2]),
            "--output",
            str(target),
            "--apply",
        ]
        with patch("app.v2.recovery_cli.codec_for_plan", return_value=self.codec):
            with redirect_stdout(StringIO()):
                code = main(argv)
        self.assertEqual(0, code)
        self.assertEqual(self.payload, target.read_bytes())
        if os.name == "posix":
            self.assertEqual(0o600, target.stat().st_mode & 0o777)

    def test_non_fragment_commands_still_require_root(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["inventory"])
        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
