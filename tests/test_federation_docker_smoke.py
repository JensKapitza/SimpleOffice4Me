"""Offline checks for the Docker fixture, not Docker runtime acceptance."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.federation_docker_peer import FIXTURE_ENV, PEERS, SIZE, _guard_local_fixture, payload
from tools.smoke_federation_docker import _events, _failure, create_tls, main, validate_download_counts


class FederationDockerFixtureTests(unittest.TestCase):
    def test_nonempty_multichunk_samples_are_distinct_and_reproducible(self):
        samples = [payload(name, case) for name in PEERS for case in ("initial", "outage")]
        self.assertTrue(all(len(sample) == SIZE and SIZE > 2 * 4 * 1024 * 1024 for sample in samples))
        self.assertEqual(len(samples), len({hashlib.sha256(sample).digest() for sample in samples}))
        self.assertEqual(samples[0], payload(PEERS[0], "initial"))

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for actual SAN certificates")
    def test_private_peer_certificates_verify_with_only_their_assigned_ip_san(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            peers = dict(zip(PEERS, ("10.213.5.2", "10.213.5.3")))
            create_tls(base, peers)
            for name, ip in peers.items():
                other = next(value for value in peers.values() if value != ip)
                command = ["openssl", "verify", "-CAfile", str(base / "ca.crt"),
                           "-purpose", "sslserver", "-verify_ip"]
                candidate = str(base / (name + ".crt"))
                self.assertEqual(0, subprocess.run([*command, ip, candidate], capture_output=True).returncode)
                self.assertNotEqual(0, subprocess.run([*command, other, candidate], capture_output=True).returncode)

    def test_log_projection_retains_retry_context_but_not_tokens_or_urls(self):
        lines = [
            "2026-11-25T18:00:01.000Z federation download deferred request=11111111-1111-4111-a111-111111111111 "
            "attempts=1 retry_in=15 error=https://synthetic/private?secret=not-for-artifact",
            "2026-11-25T18:00:01.001Z GET /blocks?proof=only-test-private Authorization: Bearer secret",
            "2026-11-25T18:00:02.000Z Traceback sensitive raw line",
        ]
        self.assertEqual(list(_events("\n".join(lines))), [
            {"kind": "download-deferred", "utc": "2026-11-25T18:00:01.000Z",
             "job": "11111111-1111-4111-a111-111111111111", "attempts": 1, "retry_in": 15},
            {"kind": "application-exception", "utc": "2026-11-25T18:00:02.000Z"},
        ])
        self.assertEqual({"exit": 1, "class": "urllib.error.HTTPError", "http_status": 404},
                         _failure(SimpleNamespace(returncode=1, stderr=
                             "urllib.error.HTTPError: HTTP Error 404: hidden/https://private?token=secret")))

    def test_scope_requires_actual_multiblock_diagnostic_evidence(self):
        job = {"job": "fixture-only"}
        snapshot = {
            "queue": [{**job, "status": "complete"}],
            "events": [{**job, "kind": "download_completed"}],
        }
        with self.assertRaisesRegex(AssertionError, "multiple"):
            validate_download_counts(snapshot, job)
        snapshot["events"].append({**job, "kind": "scoped_content_blocks_reused",
                                   "network_blocks": 3, "network_bytes": SIZE})
        validate_download_counts(snapshot, job)

    def test_missing_docker_is_failure_never_a_simulated_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            argv = ["docker-acceptance", "--image", "unused", "--html-deps", directory,
                    "--artifacts", directory]
            with patch("sys.argv", argv), patch("tools.smoke_federation_docker.shutil.which", return_value=None):
                self.assertEqual(1, main())
            self.assertIn("No fallback run", (target / "unavailable.txt").read_text())


    def test_local_fixture_preparation_refuses_a_normal_environment(self):
        with patch.dict("os.environ", {FIXTURE_ENV: ""}):
            with self.assertRaisesRegex(AssertionError, "disposable"):
                _guard_local_fixture({"run": "so-fed-deployment-123456789abc"}, PEERS[0])
