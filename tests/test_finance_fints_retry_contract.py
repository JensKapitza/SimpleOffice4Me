"""Contract checks for python-fints SCA retry serialization."""
from __future__ import annotations

import base64
import importlib.util
import unittest

from app.finance_fints import is_tan_response
from app.finance_fints_balances import _restore_retry_response


@unittest.skipUnless(importlib.util.find_spec("fints"), "requires the optional banking extra")
class FinTSRetrySerializationTests(unittest.TestCase):
    def test_need_tan_round_trip_restores_decoupled_mode_from_protected_payload(self):
        from fints.client import NeedTANResponse
        from fints.segments.accounts import HKSPA1
        from fints.segments.auth import HITAN2

        original = NeedTANResponse(
            command_seg=HKSPA1(),
            tan_request=HITAN2(tan_process="2", challenge="Approve the balance read"),
            resume_method="_get_sepa_accounts",
            decoupled=True,
        )
        retry_payload = base64.b64encode(original.get_data()).decode("ascii")

        restored = _restore_retry_response(retry_payload, decoupled=True)

        self.assertTrue(is_tan_response(restored))
        self.assertTrue(restored.decoupled)
        self.assertEqual("Approve the balance read", restored.challenge)


if __name__ == "__main__":
    unittest.main()
