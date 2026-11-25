from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase
from uuid import uuid4

from app.android_paired_folder_session import PairedFolderSession, folder_profile
from app.device_source_grants import GrantDenied
from tests.test_device_source_grants import PcKey


class SyntheticSaf:
    def __init__(self):
        self.queries = []
        self.online = True
        self.entries = ["Invoice BLEUJAY.pdf", "Meeting notes.txt"]

    def readNamesForPc(self, query):
        self.queries.append(query)
        if not self.online:
            return "{}"
        return json.dumps({
            "status": "ready", "names": [name for name in self.entries if query.casefold() in name.casefold()],
            "uri": "content://fixture/private-id", "path": "/unshared/parent", "limited": False,
        })


class PairedFolderSessionTests(TestCase):
    def setUp(self):
        self.now = 500
        self.state = PairedFolderSession(clock=lambda: self.now)
        self.addCleanup(self.state._pool.shutdown)
        self.owner = 3
        self.profile = folder_profile(b"fake local application key", self.owner)
        self.generation = uuid4().hex
        self.tab = uuid4().hex
        self.auth = (self.profile, self.owner, self.tab)
        self.adapter = SyntheticSaf()
        self.browser = PcKey()
        self.code = hashlib.sha256(self.browser.public).hexdigest()

    def pair(self):
        self.state.phone_enable(self.profile, self.generation)
        challenge = self.state.begin(*self.auth, self.browser.public)
        self.state.prove(*self.auth, challenge.id, self.browser.sign(challenge.message))
        self.state.phone_pair(self.profile, self.generation, self.code)

    def share(self):
        self.state.phone_share(self.profile, self.generation, self.code, self.adapter)

    def signed(self, term="bleujay"):
        challenge = self.state.start_read(*self.auth, term)
        return challenge.id, self.browser.sign(challenge.message), term

    def test_pair_and_per_source_are_separate_and_only_matching_names_leave(self):
        with self.assertRaises(GrantDenied):
            self.state.begin(*self.auth, self.browser.public)
        self.pair()
        self.assertEqual(self.state.pc_status(*self.auth), "awaiting_source")
        with self.assertRaises(GrantDenied):
            self.signed()
        self.assertEqual([], self.adapter.queries)
        self.share()
        self.assertEqual(self.state.pc_status(*self.auth), "shared")
        result = self.state.read(*self.auth, *self.signed())
        self.assertEqual({"names", "limited", "scope"}, set(result))
        self.assertEqual(["Invoice BLEUJAY.pdf"], result["names"])
        self.assertEqual(self.state.read(*self.auth, *self.signed("")),
                         {"names": [], "limited": False, "scope": result["scope"]})
        self.state.phone_unshare(self.profile, self.generation, self.code)
        self.assertEqual("awaiting_source", self.state.pc_status(*self.auth))
        with self.assertRaises(GrantDenied):
            self.signed()
        self.share()
        self.assertNotEqual(result["scope"], self.state.read(*self.auth, *self.signed(""))["scope"])

    def test_same_account_other_login_and_account_profile_do_not_inherit(self):
        self.pair()
        self.share()
        other = (self.profile, self.owner, uuid4().hex)
        for principal in (other, (folder_profile(b"fake local application key", 4), 4, self.tab)):
            with self.assertRaises(GrantDenied):
                self.state.read(*principal, *self.signed())
        self.assertEqual([], self.adapter.queries)

    def test_revoked_during_search_and_reapproved_drops_old_result(self):
        self.pair()
        self.share()
        entered, release = threading.Event(), threading.Event()
        read = self.adapter.readNamesForPc

        def delayed(query):
            entered.set()
            if not release.wait(2):
                raise AssertionError("fixture did not release source")
            return read(query)

        self.adapter.readNamesForPc = delayed
        with ThreadPoolExecutor(max_workers=1) as client:
            task = client.submit(self.state.read, *self.auth, *self.signed())
            try:
                self.assertTrue(entered.wait(2))
                self.adapter = SyntheticSaf()
                self.adapter.entries[:] = ["Different BLEUJAY.pdf"]
                self.share()  # explicit new source approval invalidates the in-flight permit
            finally:
                release.set()
            with self.assertRaises(GrantDenied):
                task.result(timeout=3)
        self.assertEqual(["Different BLEUJAY.pdf"], self.state.read(*self.auth, *self.signed())["names"])

    def test_offline_provider_retires_grant_even_after_reconnection(self):
        self.pair()
        self.share()
        self.adapter.online = False
        with self.assertRaises(GrantDenied):
            self.state.read(*self.auth, *self.signed(""))
        self.adapter.online = True
        self.assertEqual("awaiting_source", self.state.pc_status(*self.auth))
        with self.assertRaises(GrantDenied):
            self.signed()

    def test_expiry_foreground_shutdown_and_old_owner_generation_are_fail_closed(self):
        self.pair()
        self.share()
        self.now += 601
        with self.assertRaises(GrantDenied):
            self.signed()
        self.assertEqual("awaiting_source", self.state.pc_status(*self.auth))
        self.share()
        outstanding = self.signed()
        self.state.phone_stop(self.generation)
        with self.assertRaises(GrantDenied):
            self.state.read(*self.auth, *outstanding)
        generation = uuid4().hex
        self.state.phone_enable(self.profile, generation)
        self.state.phone_stop(self.generation)
        self.assertFalse(self.state.phone_status(self.profile, generation)["paired"])
