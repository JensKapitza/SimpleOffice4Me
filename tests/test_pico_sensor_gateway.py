from datetime import datetime, timedelta, timezone
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

from app.datalogger_collectors import collect_http, CollectionError
from app.datalogger_store import DataLoggerStore
from examples.serial.pico_gateway import sensor_link as wire
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
                             {"url": url, "json_path": "nodes.room_a.available"})
            run(root, once=True)
            for channel, expected in channels:
                self.assertEqual([expected], [item["value"] for item in store.samples(channel)])

            self.assertEqual([1.0], [item["value"] for item in store.samples(presence)])
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
