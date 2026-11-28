"""HTTP proof for the SMS archive range UI using no real messages."""
from datetime import datetime, timezone
import re
from pathlib import Path

from app import app
from app.sms_archive_store import SmsArchive
from tests.http_api_fixture import PublicHttpTestCase
from tests.test_sms_archive_store import record


class SmsRangeHttpProof(PublicHttpTestCase):
    def test_preview_confirmation_and_unmodified_selected_backup(self):
        archive = SmsArchive(Path(app.config["DOCUMENT_ROOT"]), "owner")
        stamp = int(datetime(2025, 6, 15, tzinfo=timezone.utc).timestamp() * 1000)
        rows = [record(i, date_ms=stamp + i, body="range-proof synthetic") for i in range(1, 52)]
        archive.append(rows[:50])
        archive.append([rows[-1], record(52, date_ms=stamp + 86_400_000)])
        endpoint = "/communication/sms/backup-range"
        csrf = self._csrf_headers(self.client, endpoint)
        dates = {"date_from": "2025-06-15", "date_to": "2025-06-15"}
        self.assertEqual(403, self.client.post(endpoint, data={**dates, "stage": "preview"}).status_code)
        response = self.client.post(endpoint, data={**dates, "stage": "preview"}, headers=csrf)
        self.assertEqual(200, response.status_code)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn("range-proof synthetic", response.text)
        self.assertIn("data-sms-range-count>51<", response.text)
        snapshot = re.search(r'name="snapshot" value="([a-f0-9]{64})"', response.text)[1]
        form = {**dates, "stage": "save", "snapshot": snapshot}
        save = self.client.post(endpoint, data=form, headers=csrf)
        self.assertEqual(303, save.status_code)
        self.assertRegex(save.location, r"^/communication/sms/export/[a-f0-9]{48}$")
        archive.append([record(53, date_ms=stamp)])
        self.assertEqual(409, self.client.post(endpoint, data=form, headers=csrf).status_code)
        backup = self.client.get(save.location)
        self.assertIn("attachment", backup.headers["Content-Disposition"])
        self.assertEqual(list(reversed(rows)), backup.json["payload"]["records"])
        self.assertEqual(backup.data, self.client.get(save.location).data)
        recovered = SmsArchive(Path(app.config["DOCUMENT_ROOT"]) / "fresh", "owner")
        self.assertEqual(51, recovered.verify_backup(backup.data)["missing"])
        self.assertEqual(51, recovered.restore(backup.data))
        self.assertEqual(0, recovered.restore(backup.data))
