from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase, skipUnless

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from flask import g

from app import app
from app.device_source_grants import GrantDenied, create_local_device_session
from tests.http_api_fixture import PublicHttpTestCase


class PcKey:
    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        )

    def sign(self, message):
        der = self.private.sign(message, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


class DeviceGrantTests(TestCase):
    def setUp(self):
        self.now = 50.0
        self.actor = 7
        self.pc, self.owner = create_local_device_session(owner_user_id=self.actor, clock=lambda: self.now)
        self.key = PcKey()
        self.source, self.other_source = uuid.uuid4().hex, uuid.uuid4().hex
        for source in (self.source, self.other_source):
            self.owner.update_source_availability(source, True)
        self.query = b'{"kind":"name","text":"synthetic invoice"}'

    def approve(self, key=None):
        browser = key or self.key
        challenge = self.pc.begin_pair(self.actor, browser.public)
        self.pc.prove_pair(self.actor, challenge.id, browser.sign(challenge.message))
        shown = self.owner.fingerprint_for(challenge.id)
        self.assertEqual(hashlib.sha256(browser.public).hexdigest(), shown)
        return self.owner.approve_device(challenge.id, shown)

    def prepare(self, device, key=None, *, action="search", source=None, payload=None):
        payload = self.query if payload is None else payload
        source = self.source if source is None else source
        challenge = self.pc.prepare_access(self.actor, device.id, source, action, payload)
        signature = (key or self.key).sign(challenge.message)
        return challenge, signature

    def test_same_account_alone_is_not_pairing_and_pairing_is_not_source_access(self):
        challenge = self.pc.begin_pair(self.actor, self.key.public)
        with self.assertRaises(GrantDenied):
            self.owner.approve_device(challenge.id, hashlib.sha256(self.key.public).hexdigest())
        signature = self.key.sign(challenge.message)
        with self.assertRaises(GrantDenied):
            self.pc.prove_pair(self.actor + 1, challenge.id, signature)
        with self.assertRaises(GrantDenied):
            self.pc.prove_pair(self.actor, challenge.id, PcKey().sign(challenge.message))
        self.pc.prove_pair(self.actor, challenge.id, signature)
        with self.assertRaises(GrantDenied):
            self.owner.approve_device(challenge.id, "0" * 64)
        fingerprint = self.owner.fingerprint_for(challenge.id)
        device = self.owner.approve_device(challenge.id, fingerprint)
        with self.assertRaises(GrantDenied):
            self.owner.approve_device(challenge.id, fingerprint)
        with self.assertRaises(GrantDenied):
            self.prepare(device)
        self.owner.grant_source(device.id, self.source, frozenset({"search"}), 60)
        with self.assertRaises(GrantDenied):
            self.prepare(device, action="open")
        with self.assertRaises(GrantDenied):
            self.prepare(device, source=self.other_source)
        with self.assertRaises(GrantDenied):
            self.owner.grant_source(device.id, self.source, frozenset({"write"}), 60)

    def test_each_access_needs_matching_session_key_scoped_payload_and_one_use(self):
        device = self.approve()
        self.owner.grant_source(device.id, self.source, frozenset({"search"}), 60)
        challenge, signature = self.prepare(device)
        with self.assertRaises(GrantDenied):
            self.pc.verify_access(12, challenge.id, signature, self.query)
        with self.assertRaises(GrantDenied):
            self.pc.verify_access(self.actor, challenge.id, signature, b"different bound request")
        with self.assertRaises(GrantDenied):
            self.pc.verify_access(self.actor, challenge.id, signature, self.query)
        challenge, signature = self.prepare(device, PcKey())
        with self.assertRaises(GrantDenied):
            self.pc.verify_access(self.actor, challenge.id, signature, self.query)
        challenge, signature = self.prepare(device)
        permit = self.pc.verify_access(self.actor, challenge.id, signature, self.query)
        self.assertEqual(["fake synthetic result"], self.pc.deliver(
            self.actor, permit, self.query, lambda: ["fake synthetic result"],
        ))
        with self.assertRaises(GrantDenied):
            self.pc.deliver(self.actor, permit, self.query, self.fail)

    def test_expiry_of_pending_pair_challenge_and_source_grant_is_fail_closed(self):
        challenge = self.pc.begin_pair(self.actor, self.key.public)
        self.now += 120
        with self.assertRaises(GrantDenied):
            self.pc.prove_pair(self.actor, challenge.id, self.key.sign(challenge.message))
        device = self.approve()
        self.owner.grant_source(device.id, self.source, frozenset({"search"}), 45)
        challenge, signature = self.prepare(device)
        self.now += 15
        with self.assertRaises(GrantDenied):
            self.pc.verify_access(self.actor, challenge.id, signature, self.query)
        fresh, proof = self.prepare(device)
        permit = self.pc.verify_access(self.actor, fresh.id, proof, self.query)
        self.now += 5
        with self.assertRaises(GrantDenied):
            self.pc.deliver(self.actor, permit, self.query, self.fail)
        self.now += 25
        with self.assertRaises(GrantDenied):
            self.prepare(device)

    def test_each_source_and_device_revocation_does_not_grant_or_close_unrelated_access(self):
        first = self.approve()
        second_key = PcKey()
        second = self.approve(second_key)
        for device in (first, second):
            for source in (self.source, self.other_source):
                self.owner.grant_source(device.id, source, frozenset({"search"}), 55)
        self.owner.revoke_source(first.id, self.source)
        with self.assertRaises(GrantDenied):
            self.prepare(first)
        self.prepare(first, source=self.other_source)
        self.prepare(second, second_key)
        self.owner.revoke_device(first.id)
        with self.assertRaises(GrantDenied):
            self.prepare(first, source=self.other_source)
        self.owner.remove_source(self.source)
        with self.assertRaises(GrantDenied):
            self.prepare(second, second_key)
        self.prepare(second, second_key, source=self.other_source)

    def test_revoke_mid_read_discards_results_and_new_approval_cannot_revalidate_them(self):
        device = self.approve()
        self.owner.grant_source(device.id, self.source, frozenset({"search"}), 55)
        challenge, signature = self.prepare(device)
        permit = self.pc.verify_access(self.actor, challenge.id, signature, self.query)
        loading, may_return = threading.Event(), threading.Event()

        def load():
            loading.set()
            self.assertTrue(may_return.wait(3))
            return "old result must not be sent"
        with ThreadPoolExecutor(max_workers=1) as pool:
            queued = pool.submit(self.pc.deliver, self.actor, permit, self.query, load)
            self.assertTrue(loading.wait(3))
            self.owner.revoke_source(device.id, self.source)
            self.owner.grant_source(device.id, self.source, frozenset({"search"}), 55)
            may_return.set()
            with self.assertRaises(GrantDenied):
                queued.result(timeout=3)

    def test_unknown_or_offline_source_does_not_allow_data_or_resume_old_permits(self):
        device = self.approve()
        with self.assertRaises(GrantDenied):
            self.owner.grant_source(device.id, uuid.uuid4().hex, frozenset({"search"}), 60)
        self.owner.grant_source(device.id, self.source, frozenset({"search"}), 60)
        first, proof = self.prepare(device)
        old = self.pc.verify_access(self.actor, first.id, proof, self.query)
        next_challenge, next_proof = self.prepare(device)
        self.owner.update_source_availability(self.source, False)
        with self.assertRaises(GrantDenied):
            self.prepare(device)
        self.owner.update_source_availability(self.source, True)
        with self.assertRaises(GrantDenied):
            self.pc.deliver(self.actor, old, self.query, self.fail)
        with self.assertRaises(GrantDenied):
            self.pc.verify_access(self.actor, next_challenge.id, next_proof, self.query)
        self.prepare(device)  # same source's unexpired grant can obtain a fresh proof

    def test_restarting_discards_legacy_grants_and_paired_devices(self):
        device = self.approve()
        self.owner.grant_source(device.id, self.source, frozenset({"search"}), 60)
        challenge, signature = self.prepare(device)
        restarted, _ = create_local_device_session(owner_user_id=self.actor)
        with self.assertRaises(GrantDenied):
            restarted.verify_access(self.actor, challenge.id, signature, self.query)
        with self.assertRaises(GrantDenied):
            restarted.prepare_access(self.actor, device.id, self.source, "search", self.query)


class DeviceGrantAuthenticatedEntryTests(PublicHttpTestCase):
    @skipUnless(shutil.which("node"), "A Node runtime with WebCrypto is needed for PC proof")
    def test_real_local_authentication_plus_pc_webcrypto_without_new_flask_route(self):
        entry = "/documents/local-storage"
        with self.client:
            reply = self.client.get(entry)
            self.assertEqual(200, reply.status_code)
            actor = g.user["id"]
        pc, owner = create_local_device_session(owner_user_id=actor)
        script = r'''
          const {createInterface}=require('node:readline');
          (async()=>{
            const io=createInterface({input:process.stdin});
            const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},false,['sign','verify']);
            if (pair.privateKey.extractable) throw Error('unexpected exportable private key');
            process.stdout.write(Buffer.from(await crypto.subtle.exportKey('raw',pair.publicKey)).toString('hex')+'\n');
            for await (const line of io) {
                const signature=await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},pair.privateKey,Buffer.from(line,'hex'));
                process.stdout.write(Buffer.from(signature).toString('hex')+'\n');
            }
          })().catch(err=>{console.error(err);process.exitCode=1;});
        '''
        with subprocess.Popen(
            [shutil.which("node"), "-e", script], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, text=True, encoding="ascii",
        ) as client_key:
            try:
                public_key = bytes.fromhex(client_key.stdout.readline())
                pairing = pc.begin_pair(actor, public_key)

                def sign(challenge):
                    client_key.stdin.write(challenge.message.hex() + "\n")
                    client_key.stdin.flush()
                    return bytes.fromhex(client_key.stdout.readline())

                pc.prove_pair(actor, pairing.id, sign(pairing))
                device = owner.approve_device(pairing.id, owner.fingerprint_for(pairing.id))
                source, payload = uuid.uuid4().hex, b'{"file":"fixture"}'
                owner.update_source_availability(source, True)
                owner.grant_source(device.id, source, frozenset({"search"}), 60)
                query = pc.prepare_access(actor, device.id, source, "search", payload)
                proof = sign(query)
                with app.test_client() as anonymous:
                    self.assertEqual(302, anonymous.get(entry).status_code)
                    self.assertIsNone(g.user)
                    with self.assertRaises(GrantDenied):
                        pc.verify_access(None, query.id, proof, payload)
                verified = pc.verify_access(actor, query.id, proof, payload)
                self.assertEqual(b"synthetic only", pc.deliver(actor, verified, payload, lambda: b"synthetic only"))
                self.assertNotIn("device_source_grants", app.blueprints)
            finally:
                client_key.terminate()
                client_key.wait(timeout=5)
