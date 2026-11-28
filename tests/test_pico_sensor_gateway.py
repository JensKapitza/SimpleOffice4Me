from datetime import datetime, timedelta, timezone
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

from app import app
from app.datalogger_collectors import collect_http, CollectionError
from app.datalogger_store import DataLoggerStore
from app.datalogger_pico_status import pico_status
from app.settings_store import SettingsStore
from examples.serial.pico_gateway import sensor_link as wire
from tests.http_api_fixture import PublicHttpTestCase
from tools.datalogger_worker import run


class LocalEndpoint:
    def __init__(self, handler, allowed="127.0.0.1", port=0):
        self.server = wire.listen("127.0.0.1", port)
        self.server.settimeout(0.1)
        self.port = self.server.getsockname()[1]
        self.stop = threading.Event()

        def serve():
            while not self.stop.is_set():
                try:
                    peer, address = self.server.accept()
                except TimeoutError:
                    continue
                wire.handle_client(peer, address[0], allowed, handler)

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(4)
        self.server.close()


class PicoSensorGatewayTest(unittest.TestCase):
    def endpoint(self, handler, allowed="127.0.0.1", port=0):
        endpoint = LocalEndpoint(handler, allowed, port)
        self.addCleanup(endpoint.close)
        return endpoint

    def test_two_nodes_gateway_and_real_datalogger_worker_over_tcp(self):
        a = self.endpoint(wire.sensor_handler("room_a", lambda: 21.5))
        b = self.endpoint(wire.sensor_handler("room_b", lambda: 19.75))
        gateway = self.endpoint(wire.gateway_handler((
            ("room_a", "127.0.0.1", a.port), ("room_b", "127.0.0.1", b.port),
        ), cache_ms=0))
        url = f"http://127.0.0.1:{gateway.port}/status"
        with tempfile.TemporaryDirectory() as root:
            store = DataLoggerStore(root)
            channels = []
            for name, value in (("room_a", 21.5), ("room_b", 19.75)):
                channel = store.create_channel(name, "owner")
                config = {"url": url, "json_path": f"nodes.{name}.value"}
                store.add_source(channel, "owner", "http_json", "Pico gateway", config)
                channels.append((channel, value))
            presence = store.create_channel("room_a available", "owner")
            store.add_source(presence, "owner", "http_json", "Pico availability",
                             {"url": url, "json_path": "nodes.room_a.available", "track_availability": True})
            run(root, once=True)
            for channel, expected in channels:
                self.assertEqual([expected], [item["value"] for item in store.samples(channel)])

            self.assertEqual([1.0], [item["value"] for item in store.samples(presence)])
            self.assertEqual([], store.pico_availability_changes(presence))
            a.close()
            with build_opener(ProxyHandler({})).open(url, timeout=3) as response:
                data = json.load(response)["nodes"]
            self.assertEqual({"online": False, "available": 0}, data["room_a"])
            self.assertEqual({"online": True, "available": 1, "value": 19.75}, data["room_b"])
            def next_worker_poll(minutes):
                due = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
                with patch("app.datalogger_store.utc_now", return_value=due):
                    run(root, once=True)

            self.assertEqual(0.0, collect_http({"url": url, "json_path": "nodes.room_a.available"}, root))
            next_worker_poll(5)
            self.assertCountEqual([1.0, 0.0], [item["value"] for item in store.samples(presence)])
            self.assertEqual([False], [entry["available"] for entry in store.pico_availability_changes(presence)])
            self.assertEqual([21.5], [item["value"] for item in store.samples(channels[0][0])])
            with self.assertRaises(CollectionError):
                collect_http({"url": url, "json_path": "nodes.room_a.value"}, root)
            self.endpoint(wire.sensor_handler("room_a", lambda: 22.25), port=a.port)
            self.assertEqual(22.25, collect_http({"url": url, "json_path": "nodes.room_a.value"}, root))
            self.assertEqual(1.0, collect_http({"url": url, "json_path": "nodes.room_a.available"}, root))
            next_worker_poll(10)
            self.assertCountEqual([1.0, 0.0, 1.0], [item["value"] for item in store.samples(presence)])
            self.assertCountEqual([21.5, 22.25], [item["value"] for item in store.samples(channels[0][0])])
            self.assertCountEqual([19.75] * 3, [item["value"] for item in store.samples(channels[1][0])])
            gateway.close()
            next_worker_poll(15)
            self.assertCountEqual([1.0, 0.0, 1.0], [item["value"] for item in store.samples(presence)])
            presence_source, = store.sources(presence)
            self.assertEqual("error", presence_source["last_status"])
            self.assertEqual([True, False], [entry["available"] for entry in store.pico_availability_changes(presence)])

    def test_invalid_and_repeated_availability_do_not_create_changes(self):
        with tempfile.TemporaryDirectory() as root:
            store = DataLoggerStore(root)
            channel = store.create_channel("availability", "owner")
            gateway = self.endpoint(lambda _: (200, {"nodes": {"room_a": {"available": 3}}}))
            source = store.add_source(channel, "owner", "http_json", "Pico",
                                      {"url": f"http://127.0.0.1:{gateway.port}/status",
                                       "json_path": "nodes.room_a.available", "track_availability": True})
            run(root, once=True)
            self.assertEqual([], store.samples(channel))
            self.assertEqual("availability_invalid", store.sources(channel)[0]["last_error_code"])
            for invalid in (False, "0"):
                alternate = self.endpoint(lambda _: (200, {"nodes": {"room_a": {"available": invalid}}}))
                legacy = {"url": f"http://127.0.0.1:{alternate.port}/status", "json_path": "nodes.room_a.available"}
                self.assertEqual(0.0, collect_http(legacy, root))
                with self.assertRaisesRegex(CollectionError, "availability_invalid"):
                    collect_http({**legacy, "track_availability": True}, root)
            with patch("app.datalogger_store.utc_now", return_value="2026-11-27T12:00:01+00:00"):
                self.assertTrue(store.add_pico_availability_sample(channel, source, 1))
                self.assertTrue(store.add_pico_availability_sample(channel, source, 0))  # Same receipt is idempotent.
            with patch("app.datalogger_store.utc_now", return_value="2026-11-27T12:00:02+00:00"):
                self.assertTrue(store.add_pico_availability_sample(channel, source, 1))
            self.assertEqual([], store.pico_availability_changes(channel))
            store.set_source_enabled(source, "owner", False)
            self.assertFalse(store.add_pico_availability_sample(channel, source, 0))
            self.assertEqual([], store.pico_availability_changes(channel))

    def test_availability_and_measurement_share_one_gateway_poll_over_tcp(self):
        reads = []
        node = self.endpoint(wire.sensor_handler("room_a", lambda: reads.append(31) or 31))
        gateway = self.endpoint(wire.gateway_handler((("room_a", "127.0.0.1", node.port),), cache_ms=1000))
        url = f"http://127.0.0.1:{gateway.port}/status"
        with tempfile.TemporaryDirectory() as root:
            clock = [wire.now_ms()]
            with patch.object(wire, "now_ms", side_effect=lambda: clock[0]):
                def collect(field):
                    return collect_http({"url": url, "json_path": "nodes.room_a." + field}, root)
                self.assertEqual(31.0, collect("value"))
                self.assertEqual(1.0, collect("available"))
                self.assertEqual([31], reads)
                clock[0] += 1000
                self.assertEqual(31.0, collect("value"))
                self.assertEqual([31, 31], reads)
        # Zero cache is supported for deployments that need each request to poll freshly.
        with self.assertRaises(ValueError):
            wire.gateway_handler((("room_a", "127.0.0.1", node.port),), cache_ms=5001)

    def test_unapproved_source_cannot_read(self):
        node = self.endpoint(wire.sensor_handler("room_a", lambda: 21.5), allowed="127.0.0.2")
        with self.assertRaises(HTTPError) as failed:
            build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{node.port}/reading", timeout=3)
        self.assertEqual(403, failed.exception.code)
        failed.exception.close()

    def test_source_identity_validation_and_ip_constraint(self):
        for ip in ("8.8.8.8", "example.com", "192.168.1.999", "0.0.0.0"):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                wire.gateway_handler((("room_a", ip, 8080),))
        node = self.endpoint(wire.sensor_handler("other_id", lambda: 1))
        gateway = wire.gateway_handler((("room_a", "127.0.0.1", node.port),))
        self.assertEqual((200, {"nodes": {"room_a": {"online": False, "available": 0}}}), gateway(b"/status"))
        self.assertEqual((404, {}), gateway(b"/unknown"))

    def test_non_finite_and_boolean_values_do_not_become_readings(self):
        for value in (True, float("nan"), float("inf")):
            handler = wire.sensor_handler("room_a", lambda: value)
            self.assertEqual((503, {"online": False}), handler(b"/reading"))


class PicoSetupHttpTest(PublicHttpTestCase):
    url = "/datalogger/serial/pico-setup"

    def test_admin_form_sets_up_two_actual_nodes_and_can_add_availability_later(self):
        root = app.config["DOCUMENT_ROOT"]
        store = DataLoggerStore(root)
        a = LocalEndpoint(wire.sensor_handler("room_a", lambda: 21.5))
        self.addCleanup(a.close)
        b = LocalEndpoint(wire.sensor_handler("room_b", lambda: 19.75))
        self.addCleanup(b.close)
        gateway = LocalEndpoint(wire.gateway_handler((
            ("room_a", "127.0.0.1", a.port), ("room_b", "127.0.0.1", b.port),
        ), cache_ms=0))
        self.addCleanup(gateway.close)
        page = self.client.get("/datalogger/serial")
        self.assertIn(b'action="/datalogger/serial/pico-setup"', page.data)
        headers = self._csrf_headers(self.client, "/datalogger/serial")
        fields = {"gateway_ip": "127.0.0.1", "port": str(gateway.port), "unit": "°C"}
        response = self.client.post(self.url, data=fields, headers=headers)
        self.assertEqual((303, "/datalogger/serial#pico-status"), (response.status_code, response.location))
        existing = store.channels_for("owner")
        self.assertEqual(2, len(existing))
        self.assertEqual({"°C"}, {row["unit"] for row in existing})
        self.assertEqual([], store.channels_for("another-account"))
        self.assertTrue(all(not store.samples(row["channel_id"]) for row in existing))
        status = lambda: self.client.get("/datalogger/serial").get_data(as_text=True)
        self.assertEqual(2, status().count('data-pico-source-state="pending"'))
        run(root, once=True)
        self.assertEqual(2, status().count('data-pico-source-state="ok"'))
        self.assertEqual({21.5, 19.75}, {store.samples(row["channel_id"])[0]["value"] for row in existing})
        existing_source, = store.sources(existing[0]["channel_id"])
        store.set_source_enabled(existing_source["source_id"], "owner", False)
        for _ in range(2):
            self.assertEqual(303, self.client.post(
                self.url, data={**fields, "availability": "1", "unit": "unchanged"}, headers=headers,
            ).status_code)
        all_channels = store.channels_for("owner")
        self.assertEqual(4, len(all_channels))
        self.assertEqual(0, store.sources(existing[0]["channel_id"])[0]["enabled"])
        matches = [row for row in all_channels if not row["unit"]]
        self.assertEqual(2, len(matches))
        run(root, once=True)
        for row in matches:
            source, = store.sources(row["channel_id"])
            self.assertTrue(json.loads(source["config"])["track_availability"])
            self.assertEqual(1.0, store.samples(row["channel_id"])[0]["value"])
        self.assertEqual(2, status().count('data-pico-source-state="online"'))
        self.assertEqual(1, status().count('data-pico-source-state="paused"'))
        a.close()
        due = (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat()
        with patch("app.datalogger_store.utc_now", return_value=due):
            run(root, once=True)
        self.assertEqual(1, status().count('data-pico-source-state="offline"'))
        gateway.close()
        due = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
        with patch("app.datalogger_store.utc_now", return_value=due):
            run(root, once=True)
        self.assertEqual(3, status().count('data-pico-source-state="gateway_error"'))
        self.assertNotIn('data-pico-source-state="offline"', status())

    def test_checks_authority_csrf_and_explicit_allowlist_before_writing(self):
        root = app.config["DOCUMENT_ROOT"]
        token = self._csrf_headers(self.client, "/datalogger/serial")
        valid = {"gateway_ip": "127.0.0.1", "port": "8080"}
        self.assertEqual(403, self.client.post(self.url, data=valid).status_code)
        self._register(self.client, "reader")
        reader = app.test_client()
        self._login(reader, "reader")
        self.assertNotIn(self.url.encode(), reader.get("/datalogger/serial").data)
        self.assertEqual(403, reader.post(
            self.url, data=valid, headers=self._csrf_headers(reader, "/datalogger/serial"),
        ).status_code)
        for changed in ({"gateway_ip": "192.168.40.61"}, {"gateway_ip": "8.8.8.8"},
                        {"gateway_ip": "169.254.169.254"}, {"gateway_ip": "127.0.0.1/x"},
                        {"port": "65536"}, {"availability": "yes"}, {"unit": "x" * 41}):
            with self.subTest(fields=changed):
                response = self.client.post(self.url, headers=token, data={**valid, **changed})
                self.assertEqual(303, response.status_code)
                self.assertEqual("/datalogger/serial#pico-setup", response.location)
        self.assertEqual(400, self.client.post(self.url, headers=token, data={
            **valid, "gateway_ip": ["127.0.0.1", "192.168.40.61"],
        }).status_code)
        self.assertEqual([], DataLoggerStore(root).channels_for("owner"))

    def test_allowlisted_gateway_is_configured_without_contacting_it(self):
        root = app.config["DOCUMENT_ROOT"]
        ip = "192.168.40.61"
        SettingsStore(root).save({"datalogger": {"allowed_hosts": [ip]}}, "owner")
        headers = self._csrf_headers(self.client, "/datalogger/serial")
        with patch("app.datalogger_collectors.socket.getaddrinfo", side_effect=AssertionError("unexpected lookup")):
            reply = self.client.post(self.url, data={"gateway_ip": ip}, headers=headers)
        self.assertEqual((303, "/datalogger/serial#pico-status"), (reply.status_code, reply.location))
        store = DataLoggerStore(root)
        channels = store.channels_for("owner")
        self.assertEqual(2, len(channels))
        for channel in channels:
            source, = store.sources(channel["channel_id"])
            self.assertEqual(f"http://{ip}:8080/status", json.loads(source["config"])["url"])
            self.assertEqual([], store.samples(channel["channel_id"]))
        self.assertEqual([ip], SettingsStore(root).settings()["datalogger"]["allowed_hosts"])

    def test_status_uses_only_owned_wizard_sources_and_flags_stale_observations(self):
        root = app.config["DOCUMENT_ROOT"]
        store = DataLoggerStore(root)
        store.provision_pico_example("owner", "http://127.0.0.1:8080/status", "°C")
        store.provision_pico_example("another-account", "http://127.0.0.1:8081/status", "")
        fake = store.create_channel("Manual lookalike", "owner")
        store.add_source(fake, "owner", "http_json", "Pico-Gateway", {
            "url": "http://127.0.0.1:8082/status", "json_path": "nodes.room_a.value",
        })
        data = pico_status(store, "owner", root)
        self.assertEqual({"127.0.0.1:8080"}, set(data))
        record = data["127.0.0.1:8080"]["room_a"]["value"]
        source, = store.sources(record["channel"])
        store.add_sample(record["channel"], 21.0, "system:datalogger", source_id=source["source_id"])
        store.add_sample(record["channel"], 999.0, "owner")
        store.finish_source(source["source_id"], "ok")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        stale = pico_status(store, "owner", root, now=future)["127.0.0.1:8080"]["room_a"]["value"]
        self.assertEqual("stale", stale["state"])
        self.assertEqual(21.0, stale["value"])
        SettingsStore(root).save({"datalogger": {"allowed_hosts": ["localhost"]}}, "owner")
        denied = pico_status(store, "owner", root)["127.0.0.1:8080"]["room_a"]["value"]
        self.assertEqual("blocked", denied["state"])
        self.assertEqual(21.0, denied["value"])

    def test_failed_provisioning_does_not_leave_partial_channels(self):
        store = DataLoggerStore(app.config["DOCUMENT_ROOT"])
        audit = store._event
        failures = [None, RuntimeError("synthetic transaction interruption")]

        def fail_on_second(*args, **kwargs):
            failure = failures.pop(0)
            if failure:
                raise failure
            return audit(*args, **kwargs)

        with patch.object(store, "_event", side_effect=fail_on_second), self.assertRaises(RuntimeError):
            store.provision_pico_example("owner", "http://127.0.0.1:8080/status", "")
        self.assertEqual([], store.channels_for("owner"))
