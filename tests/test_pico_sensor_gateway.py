import json
import tempfile
import threading
import unittest
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
        )))
        url = f"http://127.0.0.1:{gateway.port}/status"
        with tempfile.TemporaryDirectory() as root:
            store = DataLoggerStore(root)
            channels = []
            for name, value in (("room_a", 21.5), ("room_b", 19.75)):
                channel = store.create_channel(name, "owner")
                config = {"url": url, "json_path": f"nodes.{name}.value"}
                store.add_source(channel, "owner", "http_json", "Pico gateway", config)
                channels.append((channel, value))
            run(root, once=True)
            for channel, expected in channels:
                self.assertEqual([expected], [item["value"] for item in store.samples(channel)])

            a.close()
            with build_opener(ProxyHandler({})).open(url, timeout=3) as response:
                data = json.load(response)["nodes"]
            self.assertEqual({"online": False}, data["room_a"])
            self.assertEqual({"online": True, "value": 19.75}, data["room_b"])
            with self.assertRaises(CollectionError):
                collect_http({"url": url, "json_path": "nodes.room_a.value"}, root)
            self.endpoint(wire.sensor_handler("room_a", lambda: 22.25), port=a.port)
            self.assertEqual(22.25, collect_http({"url": url, "json_path": "nodes.room_a.value"}, root))

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
        self.assertEqual((200, {"nodes": {"room_a": {"online": False}}}), gateway(b"/status"))
        self.assertEqual((404, {}), gateway(b"/unknown"))

    def test_non_finite_and_boolean_values_do_not_become_readings(self):
        for value in (True, float("nan"), float("inf")):
            handler = wire.sensor_handler("room_a", lambda: value)
            self.assertEqual((503, {"online": False}), handler(b"/reading"))
