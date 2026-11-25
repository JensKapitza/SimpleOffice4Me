"""Synthetic Drive provider with the real native handoff, sync and search routes."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import app
from app.google_tokens import GOOGLE_DRIVE_SCOPE
from tests.http_api_fixture import PublicHttpTestCase


class ManagedDriveSearchTests(PublicHttpTestCase):
    def test_only_synced_managed_files_become_searchable_and_open_locally(self):
        drive = "/settings/google-drive"
        auth = {"User-Agent": "SimpleOffice4Me-Android/synthetic", **self._csrf_headers(self.client, drive)}
        credential = {"access_token": "fixture-only-managed-drive-token", "scopes": [GOOGLE_DRIVE_SCOPE]}
        managed = {
            "id": "managed-file", "name": "Abrechnung.txt", "mimeType": "text/plain", "parents": ["owned-root"],
            "version": "1", "size": "31", "modifiedTime": "2026-11-25T00:00:00Z",
        }
        cloud = {
            "id": "managed-cloud", "name": "Onlinedokument Wolkenrekord", "parents": ["owned-root"], "version": "1",
            "mimeType": "application/vnd.google-apps.document", "webViewLink": "https://docs.google.com/document/d/example/view",
        }
        outside = {**managed, "id": "other-file", "name": "Fremdbestand", "parents": ["other-root"]}
        content = b"CURRENT MANAGED RECEIPT ZEBRATEST\n"

        with patch("app.google_drive_sync.DriveClient", autospec=True) as factory:
            provider = factory.return_value
            provider.find_managed_root.return_value = {"id": "owned-root"}
            provider.get_start_page_token.return_value = "fixture-cursor"
            provider.list_children.return_value = [managed, cloud]
            provider.download_file.return_value = content
            provider.list_changes.return_value = ([{"fileId": outside["id"], "file": outside}], "next-cursor")

            connect = self.client.post(drive + "/android-token", headers=auth, json={**credential, "action": "connect"})
            self.assertEqual(200, connect.status_code, connect.get_data(as_text=True))
            factory.assert_not_called()
            disabled = self.client.post(drive + "/android-token", headers=auth, json={**credential, "action": "sync"})
            self.assertEqual(200, disabled.status_code)
            self.assertEqual(0, disabled.get_json()["downloaded"])
            factory.assert_not_called()
            settings = self.client.post(drive + "/settings", headers=auth, data={
                "enabled": "1", "direction": "download_only",
            })
            self.assertEqual(302, settings.status_code)
            sync = self.client.post(drive + "/android-token", headers=auth, json={**credential, "action": "sync"})
            self.assertEqual(200, sync.status_code, sync.get_data(as_text=True))
            self.assertEqual(1, sync.get_json()["downloaded"])
            provider.list_children.assert_called_once_with("owned-root")
            provider.download_file.assert_called_once()
            self.assertEqual("managed-file", provider.download_file.call_args.args[0])

            next_sync = self.client.post(drive + "/android-token", headers=auth, json={**credential, "action": "sync"})
            self.assertEqual(200, next_sync.status_code)
            self.assertEqual(0, next_sync.get_json()["downloaded"])
            provider.list_changes.assert_called_once_with("fixture-cursor")
            provider.download_file.assert_called_once()

            full_rescan = self.client.post(drive + "/full-rescan", headers=auth)
            self.assertEqual(302, full_rescan.status_code)
            resynced = self.client.post(drive + "/android-token", headers=auth, json={**credential, "action": "sync"})
            self.assertEqual(200, resynced.status_code)
            self.assertEqual(0, resynced.get_json()["downloaded"])
            self.assertEqual(2, provider.list_children.call_count)
            self.assertEqual(2, provider.get_start_page_token.call_count)
            provider.find_managed_root.assert_called_once()
            provider.list_changes.assert_called_once()
            provider.download_file.assert_called_once()

        root = Path(app.config["DOCUMENT_ROOT"])
        self.assertEqual(content, (root / "GoogleDrive" / "Abrechnung.txt").read_bytes())
        def query(word):
            return self.client.get("/documents/quick-search/api", query_string={"q": word, "type": "document"})
        hit, = query("ZEBRATEST").get_json()["matches"]
        self.assertEqual([], query("Wolkenrekord").get_json()["matches"])
        self.assertEqual([], query("Fremdbestand").get_json()["matches"])
        settings_page = BeautifulSoup(self.client.get(drive, headers=auth).data, "html.parser")
        local = settings_page.select_one('tr[data-status="synced"] a[data-drive-local-document]')
        self.assertEqual(hit["url"], local["href"])
        search = settings_page.select_one("a[data-drive-local-search]")
        search_page = BeautifulSoup(self.client.get(search["href"]).data, "html.parser")
        self.assertIsNotNone(search_page.select_one(f'.list-group a[href="{hit["url"]}"]'))
        self.assertEqual(200, self.client.get(local["href"]).status_code)
        cloud_entry = settings_page.select_one('tr[data-status="cloud_only"]')
        self.assertIn("Wolkenrekord", cloud_entry.get_text())
        self.assertNotIn("Fremdbestand", settings_page.select_one("main").get_text())
        self.assertIsNone(cloud_entry.select_one("a[data-drive-local-document]"))
        self.assertEqual(cloud["webViewLink"], cloud_entry.select_one("a")["href"])


if __name__ == "__main__":
    unittest.main()
