import tempfile
import hashlib
from contextlib import nullcontext
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g
from bs4 import BeautifulSoup

from app import app as flask_app, site_visits
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
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

    def test_report_write_failure_keeps_metadata_and_files_clean(self):
        visit_id = self.visit["visit_id"]
        original = self.store.get(visit_id, "alice")["reports"]
        def fail_metadata_write(path, _value):
            path.with_suffix(path.suffix + ".tmp").write_text("partial", encoding="utf-8")
            raise OSError("simulated metadata write failure")

        with patch("app.site_visit_store.atomic_json_write", side_effect=fail_metadata_write):
            with self.assertRaises(OSError):
                self.store.save_report_pdf(visit_id, "alice", b"%PDF-1.4 test", [])
        self.assertEqual(original, self.store.get(visit_id, "alice")["reports"])
        report_dir = self.store.directory / visit_id / "reports"
        self.assertEqual([], list(report_dir.iterdir()))
        self.assertFalse(self.store._path(visit_id).with_suffix(".json.tmp").exists())

    def test_report_legacy_metadata_is_not_reconstructed(self):
        visit_id = self.visit["visit_id"]
        entry = self.store.record_report(visit_id, "alice", {"finding_ids": [], "sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "nicht verfügbar"):
            self.store.report_pdf_path(visit_id, "alice", entry["report_id"])

    def test_multiple_report_pdfs_keep_distinct_bytes_and_audit_entries(self):
        visit_id = self.visit["visit_id"]
        first = self.store.save_report_pdf(visit_id, "alice", b"%PDF-1.4 first", [])
        second = self.store.save_report_pdf(visit_id, "alice", b"%PDF-1.4 second", [])
        self.assertNotEqual(first["report_id"], second["report_id"])
        self.assertEqual(2, len(self.store.get(visit_id, "alice")["reports"]))
        first_path = self.store.report_pdf_path(visit_id, "alice", first["report_id"])
        second_path = self.store.report_pdf_path(visit_id, "alice", second["report_id"])
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(b"%PDF-1.4 first", first_path.read_bytes())
        self.assertEqual(b"%PDF-1.4 second", second_path.read_bytes())

    def test_report_directory_symlink_is_rejected(self):
        visit_id = self.visit["visit_id"]
        entry = self.store.save_report_pdf(visit_id, "alice", b"%PDF-1.4 test", [])
        report_dir = self.store.directory / visit_id / "reports"
        outside = Path(self.temp.name) / "reports-outside"
        report_dir.rename(outside)
        try:
            report_dir.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks are unavailable: {type(exc).__name__}")
        with self.assertRaisesRegex(ValueError, "symbolischen Link"):
            self.store.report_pdf_path(visit_id, "alice", entry["report_id"])

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

    def test_unassigned_finding_can_be_assigned_later_and_reassignment_is_validated(self):
        room = self.store.add_room(self.visit["visit_id"], "alice", {"name": "Büro 1"})
        self.store.add_asset(self.visit["visit_id"], "alice", {"name": "Switch", "room_id": room["room_id"]})
        asset = self.store.get(self.visit["visit_id"], "alice")["assets"][0]
        self.store.add_finding(self.visit["visit_id"], "alice", {"title": "Unbekannte Dose"})
        finding = self.store.get(self.visit["visit_id"], "alice")["findings"][0]
        self.assertEqual("", finding["room_id"])
        self.assertEqual("", finding["asset_id"])

        self.store.update_finding(
            self.visit["visit_id"], "alice", finding["finding_id"],
            {"room_id": room["room_id"], "asset_id": asset["asset_id"]},
        )
        assigned = self.store.get(self.visit["visit_id"], "alice")["findings"][0]
        self.assertEqual(room["room_id"], assigned["room_id"])
        self.assertEqual(asset["asset_id"], assigned["asset_id"])

        with self.assertRaisesRegex(ValueError, "Unbekannter Raum"):
            self.store.update_finding(
                self.visit["visit_id"], "alice", finding["finding_id"],
                {"room_id": "00000000-0000-0000-0000-000000000000"},
            )
        with self.assertRaisesRegex(ValueError, "Unbekanntes Gerät"):
            self.store.update_finding(
                self.visit["visit_id"], "alice", finding["finding_id"],
                {"asset_id": "00000000-0000-0000-0000-000000000000"},
            )
        unchanged = self.store.get(self.visit["visit_id"], "alice")["findings"][0]
        self.assertEqual(room["room_id"], unchanged["room_id"])
        self.assertEqual(asset["asset_id"], unchanged["asset_id"])

        self.store.update_finding(
            self.visit["visit_id"], "alice", finding["finding_id"],
            {"room_id": "", "asset_id": ""},
        )
        cleared = self.store.get(self.visit["visit_id"], "alice")["findings"][0]
        self.assertEqual("", cleared["room_id"])
        self.assertEqual("", cleared["asset_id"])

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


class SiteVisitFindingAssignmentRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.visit_id = "visit-1"
        self.record = {
            "visit_id": self.visit_id,
            "findings": [
                {"finding_id": "free", "room_id": "", "asset_id": ""},
                {"finding_id": "assigned-room", "room_id": "room-1", "asset_id": ""},
                {"finding_id": "assigned-asset", "room_id": "", "asset_id": "asset-1"},
            ],
            "rooms": [],
            "assets": [],
        }

    def _render(self, path):
        with self.app.test_request_context(path):
            g.user = {"username": "alice", "is_admin": False}
            with patch("app.site_visits._visit", return_value=self.record):
                with patch("app.site_visits.render_template", side_effect=lambda name, **context: (name, context)):
                    return site_visits.finding_assignments(visit_id=self.visit_id)

    def test_unassigned_filter_only_lists_findings_without_room_or_asset(self):
        template, context = self._render(
            f"/site-visits/{self.visit_id}/findings/assignments?scope=unassigned"
        )

        self.assertEqual("site_visits/unassigned_findings.html", template)
        self.assertEqual(["free"], [finding["finding_id"] for finding in context["findings"]])
        self.assertEqual(1, context["unassigned_finding_count"])

    def test_all_filter_keeps_partially_assigned_findings_available_for_reassignment(self):
        _, context = self._render(f"/site-visits/{self.visit_id}/findings/assignments?scope=all")

        self.assertEqual(3, len(context["findings"]))
        self.assertEqual("all", context["finding_scope"])


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


class SiteVisitHttpRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.previous = {key: flask_app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        flask_app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with flask_app.app_context():
            ensure_auth_database()
        self.client = flask_app.test_client()
        self.client.post("/auth/register", data={"username": "report-owner", "password": "report-owner-password"})
        self.client.post("/auth/login", data={"username": "report-owner", "password": "report-owner-password"})
        # Authentication fixtures use the test-only CSRF exemption; enable the
        # real browser mutation guard for every report request under test.
        flask_app.config["TEST_CSRF_PROTECTION"] = True
        self.store = SiteVisitStore(self.root)
        self.visit = self.store.create("report-owner", {"title": "Report route test"})
        finding = self.store.add_finding(self.visit["visit_id"], "report-owner", {
            "title": "Validated observation",
            "description": "The selected finding appears in the output.",
            "status": "confirmed",
            "include_in_report": "1",
        })
        self.finding_id = next(
            row["finding_id"] for row in finding["findings"]
            if row["title"] == "Validated observation"
        )

    def test_unassigned_findings_can_be_assigned_and_reassigned_through_http(self):
        room = self.store.add_room(self.visit["visit_id"], "report-owner", {"name": "Büro 1"})
        self.store.add_asset(
            self.visit["visit_id"], "report-owner",
            {"name": "Switch", "room_id": room["room_id"]},
        )
        asset = self.store.get(self.visit["visit_id"], "report-owner")["assets"][0]
        unassigned = self.store.add_finding(
            self.visit["visit_id"], "report-owner", {"title": "Freier Befund"}
        )["findings"][-1]
        self.store.add_finding(
            self.visit["visit_id"], "report-owner",
            {"title": "Befund mit Gerät", "asset_id": asset["asset_id"]},
        )

        url = f"/site-visits/{self.visit['visit_id']}/findings/assignments"
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        self.assertIn("Freier Befund", page.get_text())
        self.assertNotIn("Befund mit Gerät", page.get_text())
        room_select = page.find("select", id=f"room-{unassigned['finding_id']}")
        form = room_select.find_parent("form") if room_select else None
        self.assertIsNotNone(
            form,
            f"Room selector was not rendered for {unassigned['finding_id']}; "
            f"available selectors: {[item.get('id') for item in page.select('select[id^=room-]')]}",
        )
        action = form["action"]
        csrf = form.find("input", {"name": "_csrf_token"})["value"]

        invalid = self.client.post(
            action,
            data={
                "_csrf_token": csrf, "room_id": "unknown-room",
                "asset_id": asset["asset_id"], "status": "confirmed",
                "return_to": "assignment", "scope": "unassigned",
            },
        )
        self.assertEqual(302, invalid.status_code)
        invalid_page = self.client.get(invalid.location)
        self.assertIn("Unbekannter Raum", invalid_page.get_data(as_text=True))
        unchanged = next(
            row for row in self.store.get(self.visit["visit_id"], "report-owner")["findings"]
            if row["finding_id"] == unassigned["finding_id"]
        )
        self.assertEqual("", unchanged["room_id"])
        self.assertEqual("", unchanged["asset_id"])
        self.assertEqual("unreviewed", unchanged["status"])

        saved = self.client.post(
            action,
            data={
                "_csrf_token": csrf, "room_id": room["room_id"],
                "asset_id": asset["asset_id"], "status": "rejected",
                "return_to": "assignment", "scope": "unassigned",
            },
        )
        self.assertEqual(302, saved.status_code)
        self.assertIn("scope=unassigned", saved.location)
        saved_page = self.client.get(saved.location)
        self.assertIn("Befundzuordnung gespeichert", saved_page.get_data(as_text=True))
        assigned = next(
            row for row in self.store.get(self.visit["visit_id"], "report-owner")["findings"]
            if row["finding_id"] == unassigned["finding_id"]
        )
        self.assertEqual(room["room_id"], assigned["room_id"])
        self.assertEqual(asset["asset_id"], assigned["asset_id"])
        self.assertEqual("unreviewed", assigned["status"])
        self.assertEqual(room["room_id"], assigned["history"][-1]["after"]["room_id"])
        self.assertNotIn("status", assigned["history"][-1]["after"])

        all_findings = self.client.get(f"{url}?scope=all")
        all_page = BeautifulSoup(all_findings.get_data(as_text=True), "html.parser")
        self.assertIn("Befund mit Gerät", all_page.get_text())
        self.assertEqual(400, self.client.get(f"{url}?scope=invalid").status_code)

    def tearDown(self):
        flask_app.config.update(self.previous)
        self.temp.cleanup()

    def test_preview_and_download_return_audited_pdf(self):
        url = f"/site-visits/{self.visit['visit_id']}/report"
        preview = self.client.get(url)
        self.assertEqual(200, preview.status_code)
        page = BeautifulSoup(preview.get_data(as_text=True), "html.parser")
        form = page.find("form", action=f"/site-visits/{self.visit['visit_id']}/report.pdf")
        self.assertIsNotNone(form)
        csrf = form.find("input", {"name": "_csrf_token"})["value"]

        response = self.client.post(
            f"/site-visits/{self.visit['visit_id']}/report.pdf",
            data={"_csrf_token": csrf, "finding_id": self.finding_id},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.mimetype)
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertTrue(response.data.startswith(b"%PDF"))
        report = self.store.get(self.visit["visit_id"], "report-owner")["reports"][-1]
        self.assertEqual([self.finding_id], report["finding_ids"])
        self.assertEqual(hashlib.sha256(response.data).hexdigest(), report["sha256"])

    def test_empty_selection_generates_downloadable_audited_pdf(self):
        from io import BytesIO
        from pypdf import PdfReader
        empty_visit = self.store.create("report-owner", {"title": "Ortstermin ohne Befunde"})
        visit_id = empty_visit["visit_id"]
        preview = self.client.get(f"/site-visits/{visit_id}/report")
        self.assertEqual(200, preview.status_code)
        page = BeautifulSoup(preview.get_data(as_text=True), "html.parser")
        csrf = page.find("input", {"name": "_csrf_token"})["value"]
        response = self.client.post(
            f"/site-visits/{visit_id}/report.pdf",
            data={"_csrf_token": csrf},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.mimetype)
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"%PDF"))
        pdf = PdfReader(BytesIO(response.data))
        self.assertIn("Keine bestätigten Befunde vorhanden.", "\\n".join(page.extract_text() for page in pdf.pages))
        report = self.store.get(visit_id, "report-owner")["reports"][-1]
        self.assertEqual([], report["finding_ids"])
        self.assertEqual(hashlib.sha256(response.data).hexdigest(), report["sha256"])

    def test_saved_report_can_be_downloaded_after_store_reopen(self):
        preview = BeautifulSoup(
            self.client.get(f"/site-visits/{self.visit['visit_id']}/report").get_data(as_text=True),
            "html.parser",
        )
        csrf = preview.find("input", {"name": "_csrf_token"})["value"]
        original = self.client.post(
            f"/site-visits/{self.visit['visit_id']}/report.pdf",
            data={"_csrf_token": csrf, "finding_id": self.finding_id},
        )
        self.assertEqual(200, original.status_code)
        reopened = SiteVisitStore(self.root)
        entry = reopened.get(self.visit["visit_id"], "report-owner")["reports"][-1]
        self.assertIn("relative_path", entry)
        url = f"/site-visits/{self.visit['visit_id']}/reports/{entry['report_id']}.pdf"
        downloaded = self.client.get(url)
        self.assertEqual(200, downloaded.status_code)
        self.assertEqual(original.data, downloaded.data)
        self.assertEqual(hashlib.sha256(downloaded.data).hexdigest(), entry["sha256"])
        self.assertIn("attachment;", downloaded.headers["Content-Disposition"])
        self.assertIn("private", downloaded.headers["Cache-Control"])
        self.assertEqual(404, self.client.get(
            f"/site-visits/{self.visit['visit_id']}/reports/00000000-0000-0000-0000-000000000000.pdf"
        ).status_code)
        path = reopened.report_pdf_path(self.visit["visit_id"], "report-owner", entry["report_id"])
        path.write_bytes(b"%PDF- corrupted")
        self.assertEqual(404, self.client.get(url).status_code)
        path.unlink()
        target = self.root / "unrelated-private-file.pdf"
        target.write_bytes(original.data)
        path.symlink_to(target)
        self.assertEqual(404, self.client.get(url).status_code)
        path.unlink()
        with self.assertRaises(ValueError):
            reopened.report_pdf_path(self.visit["visit_id"], "other-owner", entry["report_id"])

    def test_report_storage_failure_returns_controlled_error_without_metadata(self):
        preview = BeautifulSoup(
            self.client.get(f"/site-visits/{self.visit['visit_id']}/report").get_data(as_text=True),
            "html.parser",
        )
        csrf = preview.find("input", {"name": "_csrf_token"})["value"]
        with patch("app.site_visit_store.atomic_json_write", side_effect=OSError("disk full")):
            response = self.client.post(
                f"/site-visits/{self.visit['visit_id']}/report.pdf",
                data={"_csrf_token": csrf, "finding_id": self.finding_id},
            )
        self.assertEqual(302, response.status_code)
        self.assertEqual([], self.store.get(self.visit["visit_id"], "report-owner")["reports"])
        report_dir = self.store.directory / self.visit["visit_id"] / "reports"
        self.assertEqual([], list(report_dir.iterdir()))
        with self.client.session_transaction() as state:
            self.assertIn("Der Bericht konnte nicht gespeichert werden", state["_flashes"][-1][1])

    def test_invalid_selection_missing_csrf_and_foreign_visit_are_rejected(self):
        url = f"/site-visits/{self.visit['visit_id']}/report.pdf"
        self.assertEqual(403, self.client.post(url, data={"finding_id": self.finding_id}).status_code)
        preview = BeautifulSoup(self.client.get(f"/site-visits/{self.visit['visit_id']}/report").get_data(as_text=True), "html.parser")
        csrf = preview.find("input", {"name": "_csrf_token"})["value"]
        rejected = self.client.post(url, data={"_csrf_token": csrf, "finding_id": "unknown-finding"})
        self.assertEqual(302, rejected.status_code, rejected.get_data(as_text=True)[:500])
        self.assertEqual([], self.store.get(self.visit["visit_id"], "report-owner")["reports"])

        outsider = flask_app.test_client()
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO user (username, password, is_admin, is_disabled, auth_version, created_at, updated_at) "
                "VALUES (?, ?, 0, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                ("report-outsider", hash_password("report-outsider-password")),
            )
            db.commit()
        with patch.dict(flask_app.config, {"TEST_CSRF_PROTECTION": False}):
            login_response = outsider.post(
                "/auth/login",
                data={"username": "report-outsider", "password": "report-outsider-password"},
            )
        self.assertEqual(302, login_response.status_code)
        self.assertEqual(404, outsider.get(f"/site-visits/{self.visit['visit_id']}/report").status_code)


if __name__ == "__main__":
    unittest.main()
