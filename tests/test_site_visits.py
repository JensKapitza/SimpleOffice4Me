import tempfile
from contextlib import nullcontext
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.site_visit_report import build_visit_report
from app.site_visits import _selected_report_ids
from app.site_visit_scan import parse_nmap_options, parse_scan_request, scan_authorized_private_network
from app.site_visit_store import SiteVisitStore


class SiteVisitStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config["SECRET_KEY"] = "unit-test-secret"
        self.context = self.app.app_context()
        self.context.push()
        self.store = SiteVisitStore(Path(self.temp.name))
        self.visit = self.store.create("alice", {"title": "Büroaufnahme", "site": "Nord", "notes": "Router unbekannt"})

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    def test_free_fields_rooms_and_device_map_are_preserved(self):
        room = self.store.add_room(self.visit["visit_id"], "alice", {"name": "Serverraum", "floor": "UG"})
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "Altgerät", "kind": "Server", "room_id": room["room_id"], "power_w": "", "description": "Typenschild fehlt"})
        asset = self.store.get(self.visit["visit_id"], "alice")["assets"][0]
        fetched = self.store.get(self.visit["visit_id"], "alice")
        self.assertEqual("Typenschild fehlt", fetched["assets"][0]["description"])
        self.assertIsNone(fetched["assets"][0]["power_w"])
        self.assertEqual(1, SiteVisitStore.power_totals(fetched)[0]["unknown_devices"])
        self.assertEqual(asset["asset_id"], fetched["assets"][0]["asset_id"])

    def test_circuit_load_warns_only_when_recorded_limit_is_exceeded(self):
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "PC", "circuit": "Steckdosenleiste A", "power_w": 120, "circuit_limit_w": 150})
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "Monitor", "circuit": "Steckdosenleiste A", "power_w": 60})
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "Unbekanntes Netzteil", "circuit": "Steckdosenleiste A"})
        totals = SiteVisitStore.power_totals(self.store.get(self.visit["visit_id"], "alice"))
        self.assertTrue(totals[0]["over_limit"])
        self.assertEqual(-30, totals[0]["remaining_w"])
        self.assertEqual(1, totals[0]["unknown_devices"])

    def test_credentials_are_encrypted_and_excluded_from_public_visit(self):
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "Router"})
        asset = self.store.get(self.visit["visit_id"], "alice")["assets"][0]
        self.store.set_credentials(self.visit["visit_id"], "alice", asset["asset_id"], "admin", "secret-pass", "Sticker on device")
        raw = self.store._read(self.store._path(self.visit["visit_id"]))
        self.assertNotIn("secret-pass", str(raw))
        public = self.store.get(self.visit["visit_id"], "alice")["assets"][0]
        self.assertNotIn("credentials", public)
        self.assertTrue(public["has_credentials"])
        self.assertEqual("secret-pass", self.store.credentials(self.visit["visit_id"], "alice", asset["asset_id"])["password"])

    def test_finding_chain_rejects_cycles_and_allows_curation(self):
        self.store.add_finding(self.visit["visit_id"], "alice", {"title": "PC ohne Strom", "evidence_class": "observed"})
        self.store.add_finding(self.visit["visit_id"], "alice", {"title": "Netzteil defekt", "evidence_class": "derived"})
        first, second = self.store.get(self.visit["visit_id"], "alice")["findings"]
        self.store.add_edge(self.visit["visit_id"], "alice", first["finding_id"], second["finding_id"], "causes", "Spannung fehlt")
        with self.assertRaisesRegex(ValueError, "zyklus"):
            self.store.add_edge(self.visit["visit_id"], "alice", second["finding_id"], first["finding_id"], "causes")
        self.store.update_finding(self.visit["visit_id"], "alice", second["finding_id"], {"status": "confirmed", "include_in_report": "1"})
        self.store.update_finding(self.visit["visit_id"], "alice", first["finding_id"], {"status": "not_assessable", "include_in_report": "1"})
        rows = self.store.get(self.visit["visit_id"], "alice")["findings"]
        self.assertTrue(rows[1]["include_in_report"])
        self.assertFalse(rows[0]["include_in_report"])
        self.assertEqual(1, len(rows[0]["history"]))

    def test_scan_merges_ports_into_existing_manual_device_atomically(self):
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "Gateway", "ip": "192.168.1.1", "ports": "22,443"})
        scan = {"cidr": "192.168.1.0/30", "ports": [80], "probed_hosts": 2, "device_count": 1, "scanned_at": "2026-09-28T00:00:00Z", "scanner": "tcp-connect", "limitations": "TCP only", "devices": [{"ip": "192.168.1.1", "ports": [{"port": 80, "protocol": "tcp", "state": "open", "source": "scan", "observed_at": "2026-09-28T00:00:00Z"}]}]}
        self.store.apply_scan(self.visit["visit_id"], "alice", scan)
        record = self.store.get(self.visit["visit_id"], "alice")
        self.assertEqual(1, len(record["assets"]))
        self.assertEqual({22, 80, 443}, {port["port"] for port in record["assets"][0]["ports"]})
        self.assertEqual(1, len(record["scans"]))

    def test_snapshots_compare_device_additions_without_credentials(self):
        before = self.store.add_snapshot(self.visit["visit_id"], "alice", "Eingang")
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "Switch", "ip": "192.168.1.2"})
        asset = self.store.get(self.visit["visit_id"], "alice")["assets"][0]
        self.store.set_credentials(self.visit["visit_id"], "alice", asset["asset_id"], "operator", "pw")
        after = self.store.add_snapshot(self.visit["visit_id"], "alice", "Nach Reparatur")
        diff = self.store.snapshot_diff(self.visit["visit_id"], "alice", before["snapshot_id"], after["snapshot_id"])
        self.assertEqual("Switch", diff["added"][0]["item"]["name"])
        self.assertNotIn("credentials", diff["added"][0]["item"])

    def test_report_is_explicitly_curated_and_does_not_leak_credentials(self):
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "Router"})
        asset = self.store.get(self.visit["visit_id"], "alice")["assets"][0]
        self.store.set_credentials(self.visit["visit_id"], "alice", asset["asset_id"], "admin", "never-in-report")
        self.store.add_finding(self.visit["visit_id"], "alice", {"title": "Port 443 erreichbar", "status": "confirmed", "include_in_report": "1", "evidence_class": "observed"})
        record = self.store.get(self.visit["visit_id"], "alice")
        finding_id = record["findings"][0]["finding_id"]
        self.assertEqual([finding_id], _selected_report_ids(record, []))
        self.assertEqual([], _selected_report_ids(record, [], defaults=False))
        payload, digest, included = build_visit_report(record, [finding_id])
        self.assertTrue(payload.startswith(b"%PDF"))
        self.assertEqual([finding_id], included)
        self.assertEqual(64, len(digest))
        self.assertNotIn(b"never-in-report", payload)

    def test_actor_isolation_and_unsupported_upload_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "nicht gefunden"):
            self.store.get(self.visit["visit_id"], "bob")
        with self.assertRaisesRegex(ValueError, "Dateityp"):
            self.store.add_attachment(self.visit["visit_id"], "alice", "x.html", "text/html", b"<script>", "visit", self.visit["visit_id"])


class SiteVisitScanTests(unittest.TestCase):
    def test_scan_requires_explicit_approval_and_private_bounded_network(self):
        with self.assertRaisesRegex(ValueError, "Freigabe"):
            parse_scan_request("192.168.1.0/24", "80", False)
        with self.assertRaisesRegex(ValueError, "private"):
            parse_scan_request("8.8.8.0/24", "80", True)
        with self.assertRaisesRegex(ValueError, "maximal"):
            parse_scan_request("10.0.0.0/16", "80", True)
        network, ports = parse_scan_request("192.168.1.0/24", "80,443,80", True)
        self.assertEqual(256, network.num_addresses)
        self.assertEqual([80, 443], ports)

    def test_named_port_groups_combine_with_custom_ports_and_are_bounded(self):
        _, ports = parse_scan_request("192.168.1.0/24", "1234,8000-8002", True, ["web", "mail"])
        self.assertEqual([80, 443, 8080, 8443, 25, 110, 143, 465, 587, 993, 995, 1234, 8000, 8001, 8002], ports)
        with self.assertRaisesRegex(ValueError, "Unbekannte Portgruppe"):
            parse_scan_request("192.168.1.0/24", "80", True, ["all_ports"])
        with self.assertRaisesRegex(ValueError, "Maximal"):
            parse_scan_request("192.168.1.0/24", "1,2,3,4,5,6,7", True, ["web", "mail", "file_sharing", "remote_access", "databases"])

    def test_nmap_like_option_allowlist_maps_to_bounded_scanner_settings(self):
        options = parse_nmap_options("nmap -sT -n -Pn --open -r -p 8080,9000-9002 -T4")
        self.assertEqual("8080,9000-9002", options["ports"])
        self.assertTrue(options["replace_ports"])
        self.assertEqual(4, options["timing"])
        self.assertEqual(24, options["workers"])
        fast = parse_nmap_options("-F")
        self.assertTrue(fast["replace_ports"])
        self.assertEqual(32, len(fast["ports"].split(",")))
        self.assertEqual(5, len(parse_nmap_options("--top-ports 5")["ports"].split(",")))
        with self.assertRaisesRegex(ValueError, "Nicht unterstützte"):
            parse_nmap_options("--script vuln")
        with self.assertRaisesRegex(ValueError, "Nicht unterstützte"):
            parse_nmap_options("-sS")
        with self.assertRaisesRegex(ValueError, "0 bis 4"):
            parse_nmap_options("-T5")
        with self.assertRaisesRegex(ValueError, "Nicht unterstützte"):
            parse_nmap_options("$(touch /tmp/should-not-run)")
        with self.assertRaisesRegex(ValueError, "zu lang"):
            parse_nmap_options("-n " + "x" * 600)

    @patch("app.site_visit_scan.socket.create_connection")
    def test_scan_only_attempts_selected_tcp_ports(self, connect):
        from contextlib import closing
        def probe(address, timeout):
            if address == ("192.168.1.2", 443):
                return nullcontext(object())
            raise OSError("closed")
        connect.side_effect = probe
        result = scan_authorized_private_network("192.168.1.0/30", "443", True)
        self.assertEqual([{"ip": "192.168.1.2", "ports": [unittest.mock.ANY]}], result["devices"])
        self.assertEqual(2, connect.call_count)
        self.assertEqual("tcp", result["devices"][0]["ports"][0]["protocol"])
        self.assertEqual("TCP connect (-T3)", result["scanner"])


if __name__ == "__main__":
    unittest.main()
