"""Public HTTP export contracts with independently fixed CSV expectations."""
import csv
import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.object_store import ObjectStore
from app.library.store import LibraryStore


class LibraryExportContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        saved = {key: app.config.get(key) for key in ('DATABASE', 'DOCUMENT_ROOT', 'TESTING')}
        self.addCleanup(lambda: app.config.update(saved))
        self.documents = self.root / 'local'
        app.config.update(TESTING=True, DATABASE=str(self.root / 'users.sqlite'),
                          DOCUMENT_ROOT=str(self.documents))
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('csv-admin','unused',1)")
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('csv-reader','unused',0)")
            db.commit()
            self.admin = db.execute("SELECT id FROM user WHERE username='csv-admin'").fetchone()[0]
            self.reader = db.execute("SELECT id FROM user WHERE username='csv-reader'").fetchone()[0]
        self.store = ObjectStore(self.documents)
        self.book = self.store.create({'name': '=2+2', 'type': 'book', 'identifier': '9780000000001',
                                      'fields': {'authors': 'Müller; Ångström', 'genre': '\x00=1+1',
                                                 'hidden': 'fake-private-not-exported'}}, 'csv-admin')
        self.other = self.store.create({'name': 'Anderes Buch', 'type': 'book'}, 'csv-admin')
        self.nonbook = self.store.create({'name': 'Kein Buch', 'type': 'device'}, 'csv-admin')
        self.foreign_root = self.root / 'foreign'
        self.foreign = ObjectStore(self.foreign_root).create({'name': 'Fremder Instanzbestand', 'type': 'book'},
                                                           'foreign-admin')
        self.client = app.test_client()
        self.login(self.admin)

    def login(self, identifier):
        with self.client.session_transaction() as session:
            session['user_id'] = identifier

    def rows(self, response):
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.data.startswith(b'\xef\xbb\xbf'))
        return list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))

    def test_unprivileged_account_cannot_escalate_export_via_parameters(self):
        """A real non-admin account remains forbidden even with forged roles, roots and IDs."""
        self.login(self.reader)
        result = self.client.get('/library/export/books.csv', query_string={
            'admin': '1', 'root': str(self.foreign_root), 'object_id': self.foreign['object_id']})
        self.assertEqual(403, result.status_code)
        self.assertNotIn(b'Fremder Instanzbestand', result.data)

    def test_instance_root_cannot_be_overridden_by_export_parameters(self):
        """The configured local catalog is exported; another configured root is never read."""
        result = self.client.get('/library/export/books.csv', query_string={'root': str(self.foreign_root)})
        rows = self.rows(result)
        self.assertEqual({"'=2+2", 'Anderes Buch'}, {row['Titel'] for row in rows})
        self.assertNotIn('fake-private-not-exported', result.get_data(as_text=True))
        self.assertEqual('no-store', result.headers['Cache-Control'])
        self.assertEqual('nosniff', result.headers['X-Content-Type-Options'])

    def test_selected_export_deduplicates_only_authorized_local_books(self):
        """Repeated valid IDs export exactly the chosen book once, with literal safe values."""
        result = self.client.get('/library/export/books.csv', query_string=[
            ('object_id', self.book['object_id']), ('object_id', self.book['object_id'])])
        rows = self.rows(result)
        self.assertEqual(1, len(rows))
        self.assertEqual("'=2+2", rows[0]['Titel'])
        self.assertEqual('Müller; Ångström', rows[0]['Autoren'])
        self.assertEqual("'\x00=1+1", rows[0]['Genre'])
        self.assertEqual('9780000000001', rows[0]['ISBN'])

    def test_foreign_missing_traversal_and_nonbook_ids_fail_before_csv(self):
        """Invalid selections yield 404 rather than a partial or out-of-root export."""
        for identifier in (self.foreign['object_id'], '../foreign/object', '999999999', self.nonbook['object_id']):
            with self.subTest(identifier=identifier):
                result = self.client.get('/library/export/books.csv', query_string={'object_id': identifier})
                self.assertEqual(404, result.status_code)
                self.assertNotIn(b'Fremder Instanzbestand', result.data)
                self.assertNotIn(b'\xef\xbb\xbf', result.data)

    def test_full_export_exceeds_ui_limit_and_keeps_nonbooks_out(self):
        """503 local books produce 503 rows, independent of the 500-row UI limit."""
        for number in range(501):
            self.store.create({'name': f'Zusatzbuch {number}', 'type': 'book'}, 'csv-admin')
        rows = self.rows(self.client.get('/library/export/books.csv'))
        self.assertEqual(503, len(rows))
        self.assertIn('Zusatzbuch 500', {row['Titel'] for row in rows})
        self.assertNotIn('Kein Buch', {row['Titel'] for row in rows})

    def test_completed_export_is_visible_as_success_in_public_audit(self):
        """A fully consumed CSV records its actual two-row result in the admin audit API."""
        self.assertEqual(2, len(self.rows(self.client.get('/library/export/books.csv'))))
        audit = self.client.get('/admin/logs/export?format=csv&action=library_books_export')
        self.assertEqual(200, audit.status_code)
        records = list(csv.DictReader(io.StringIO(audit.data.decode('utf-8-sig'))))
        events = [record for record in records if record['record_type'] == 'event']
        self.assertEqual(['success'], [event['outcome'] for event in events])
        self.assertIn('"rows": 2', events[0]['detail_json'])

    def test_existing_assignment_is_exported_without_requiring_mirror_fields(self):
        """Canonical shelf assignments remain correct for legacy books without copied location fields."""
        library = LibraryStore(self.documents)
        room = library.create_location('Lesezimmer', 'csv-admin', code='ROOM-ALPHA')
        shelf = library.create_location('Regal', 'csv-admin', parent_id=room['location_id'], code='SHELF-ALPHA')
        library.assign(self.other['object_id'], shelf['location_id'], 'csv-admin')
        rows = self.rows(self.client.get('/library/export/books.csv', query_string={'object_id': self.other['object_id']}))
        self.assertEqual(1, len(rows))
        self.assertEqual('Lesezimmer / Regal', rows[0]['Standort'])
        self.assertEqual('SHELF-ALPHA', rows[0]['Regalcode'])
