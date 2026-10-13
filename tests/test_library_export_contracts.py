"""Public HTTP export contracts with independently fixed CSV expectations."""
import csv
import io
import json
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
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

    def test_unprivileged_account_cannot_access_bibliography_formats(self):
        self.login(self.reader)
        for path in ('/library/export/books.ris', '/library/export/books.bib', '/library/export/books.calibre.zip'):
            with self.subTest(path=path):
                result = self.client.get(path, query_string={'admin': '1', 'object_id': self.book['object_id']})
                self.assertEqual(403, result.status_code)
                self.assertNotIn(b'=2+2', result.data)

    def test_library_page_exposes_formats_only_to_an_administrator(self):
        admin_page = self.client.get('/library').get_data(as_text=True)
        self.assertIn('/library/export/books.ris', admin_page)
        self.assertIn('/library/export/books.bib', admin_page)
        self.assertIn('/library/export/books.calibre.zip', admin_page)
        self.assertIn('formaction="/library/export/books.calibre.zip"', admin_page)
        self.login(self.reader)
        reader_page = self.client.get('/library').get_data(as_text=True)
        self.assertNotIn('/library/export/books.ris', reader_page)
        self.assertNotIn('/library/export/books.bib', reader_page)
        self.assertNotIn('/library/export/books.calibre.zip', reader_page)

    def test_calibre_archive_contains_metadata_csv_and_one_opf_per_selected_book(self):
        """The public bundle exports selected local metadata in Calibre CSV and OPF forms."""
        book = self.store.create({
            'name': '=Calibre & {Book}', 'type': 'book', 'identifier': '9780000000023',
            'description': 'Public\x01 description',
            'fields': {
                'authors': 'Müller; Ångström', 'genre': 'History', 'series': 'Archive',
                'series_number': '2', 'publisher': 'Local Press', 'year': '2023',
                'language': 'de', 'edition': 'Second', 'pages': '321',
                'hidden': 'private-field-must-not-export',
            },
        }, 'csv-admin')
        response = self.client.get('/library/export/books.calibre.zip', query_string={
            'object_id': book['object_id'],
        })
        self.addCleanup(response.close)

        self.assertEqual(200, response.status_code)
        self.assertEqual('application/zip', response.mimetype)
        self.assertEqual('no-store', response.headers['Cache-Control'])
        self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])
        self.assertIn('library-books-calibre.zip', response.headers['Content-Disposition'])
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            members = archive.namelist()
            opf_names = [name for name in members if name.startswith('opf/') and name.endswith('.opf')]
            self.assertEqual(2, len(members))
            self.assertEqual(1, len(opf_names))
            csv_rows = list(csv.DictReader(io.StringIO(archive.read('metadata.csv').decode('utf-8-sig'))))
            self.assertEqual([{
                'title': "'=Calibre & {Book}", 'authors': 'Müller & Ångström',
                'series': 'Archive', 'series_index': '2', 'publisher': 'Local Press',
                'published': '2023', 'identifiers': 'isbn:9780000000023',
                'tags': 'History', 'comments': 'Public description; Inventory ID: ' + book['display_id'] + '; Pages: 321; Edition: Second',
                'language': 'de',
            }], csv_rows)
            opf = ET.fromstring(archive.read(opf_names[0]))
            dc = '{http://purl.org/dc/elements/1.1/}'
            opf_ns = '{http://www.idpf.org/2007/opf}'
            self.assertEqual('=Calibre & {Book}', opf.findtext(f'.//{dc}title'))
            self.assertEqual(['Müller', 'Ångström'], [node.text for node in opf.findall(f'.//{dc}creator')])
            self.assertIn('Edition: Second', opf.findtext(f'.//{dc}description'))
            self.assertEqual('9780000000023', next(node.text for node in opf.findall(f'.//{dc}identifier')
                                                   if node.attrib.get('id') == 'isbn'))
            calibre_meta = {node.attrib['name']: node.attrib['content'] for node in opf.findall(f'.//{opf_ns}meta')}
            self.assertEqual('Archive', calibre_meta['calibre:series'])
            self.assertEqual('2', calibre_meta['calibre:series_index'])
            serialized = archive.read(opf_names[0]).decode('utf-8')
            self.assertIn('&amp;', serialized)
            self.assertNotIn('\x01', serialized)
            self.assertNotIn('private-field-must-not-export', serialized)
        self.assertNotIn('private-field-must-not-export', response.data.decode('latin-1'))
        response.close()
        audit_response = self.client.get('/admin/logs/export?format=csv&action=library_books_export')
        audit_rows = list(csv.DictReader(io.StringIO(audit_response.data.decode('utf-8-sig'))))
        events = [row for row in audit_rows if row['record_type'] == 'event']
        self.assertEqual(1, len(events))
        detail = json.loads(events[0]['detail_json'])
        self.assertEqual(('calibre_csv_opf', 1, True),
                         (detail['format'], detail['rows'], detail['complete']))

    def test_calibre_archive_rejects_foreign_and_over_limit_selections(self):
        foreign = self.client.get('/library/export/books.calibre.zip', query_string={
            'object_id': self.foreign['object_id'],
        })
        self.assertEqual(404, foreign.status_code)
        too_many = self.client.get('/library/export/books.calibre.zip', query_string=[
            ('object_id', self.book['object_id']) for _ in range(501)
        ])
        self.assertEqual(400, too_many.status_code)

    def test_calibre_archive_without_selection_exports_all_books_only(self):
        response = self.client.get('/library/export/books.calibre.zip')
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            csv_rows = list(csv.DictReader(io.StringIO(archive.read('metadata.csv').decode('utf-8-sig'))))
            opf_names = [name for name in archive.namelist() if name.startswith('opf/') and name.endswith('.opf')]
            self.assertEqual(2, len(csv_rows))
            self.assertEqual(2, len(opf_names))
            self.assertEqual({"'=2+2", 'Anderes Buch'}, {row['title'] for row in csv_rows})
            for name in opf_names:
                opf = ET.fromstring(archive.read(name))
                self.assertEqual('und', opf.findtext('.//{http://purl.org/dc/elements/1.1/}language'))

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
        self.assertEqual("'=1+1", rows[0]['Genre'])
        self.assertEqual('9780000000001', rows[0]['ISBN'])

    def test_selected_ris_export_uses_book_tags_and_retains_inventory_id(self):
        response = self.client.get('/library/export/books.ris', query_string=[
            ('object_id', self.book['object_id']), ('object_id', self.book['object_id']),
        ])
        self.assertEqual(200, response.status_code)
        self.assertEqual('no-store', response.headers['Cache-Control'])
        self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])
        self.assertIn('application/x-research-info-systems', response.headers['Content-Type'])
        expected = (
            'TY  - BOOK\r\n'
            f'ID  - {self.book["object_id"]}\r\n'
            'T1  - =2+2\r\n'
            'AU  - Müller\r\n'
            'AU  - Ångström\r\n'
            'SN  - 9780000000001\r\n'
            'KW  - =1+1\r\n'
            f'N1  - Inventory ID: {self.book["display_id"]}\r\n'
            'ER  -\r\n\r\n'
        )
        self.assertEqual('\ufeff' + expected, response.get_data(as_text=True))
        self.assertNotIn('fake-private-not-exported', response.get_data(as_text=True))

    def test_selected_bibtex_export_escapes_metadata_and_omits_unmapped_fields(self):
        response = self.client.get('/library/export/books.bib', query_string={
            'object_id': self.book['object_id'],
        })
        self.assertEqual(200, response.status_code)
        self.assertIn('application/x-bibtex', response.headers['Content-Type'])
        self.assertIn('filename="library-books.bib"', response.headers['Content-Disposition'])
        expected = (
            '\ufeff'
            f'@book{{simpleoffice_{self.book["object_id"]},\n'
            '  title = {=2+2},\n'
            '  author = {Müller and Ångström},\n'
            f'  note = {{Inventory ID: {self.book["display_id"]}; ISBN: 9780000000001; Genre: =1+1}}\n'
            '}\n\n'
        )
        self.assertEqual(expected, response.get_data(as_text=True))
        self.assertNotIn('fake-private-not-exported', response.get_data(as_text=True))

    def test_bibliography_export_rejects_foreign_missing_and_nonbook_selections(self):
        for path in ('/library/export/books.ris', '/library/export/books.bib', '/library/export/books.calibre.zip'):
            for identifier in (self.foreign['object_id'], '../foreign/object', '999999999', self.nonbook['object_id']):
                with self.subTest(path=path, identifier=identifier):
                    result = self.client.get(path, query_string={'object_id': identifier})
                    self.assertEqual(404, result.status_code)
                    self.assertNotIn('Fremder Instanzbestand', result.get_data(as_text=True))
                    self.assertNotIn('TY  - BOOK', result.get_data(as_text=True))
                    self.assertNotIn('@book{', result.get_data(as_text=True))

    def test_all_export_formats_reject_more_than_500_selection_references(self):
        query = [('object_id', self.book['object_id'])] * 501
        for path in ('/library/export/books.csv', '/library/export/books.ris', '/library/export/books.bib',
                     '/library/export/books.calibre.zip'):
            with self.subTest(path=path):
                result = self.client.get(path, query_string=query)
                self.assertEqual(400, result.status_code)

    def test_exported_text_cannot_inject_ris_records_or_bibtex_fields(self):
        hostile = self.store.create({
            'name': 'Title {one}\n@misc{injected}',
            'type': 'book',
            'fields': {
                'authors': 'Alice; Bob',
                'publisher': 'Press\nyear={2099}',
                'pages': '224',
                'hidden': 'never exported',
            },
        }, 'csv-admin')
        ris = self.client.get('/library/export/books.ris', query_string={'object_id': hostile['object_id']})
        self.assertIn('T1  - Title {one} @misc{injected}', ris.get_data(as_text=True))
        self.assertIn('PB  - Press year={2099}', ris.get_data(as_text=True))
        self.assertIn('AU  - Alice\r\nAU  - Bob', ris.get_data(as_text=True))
        self.assertNotIn('\n@misc{injected}', ris.get_data(as_text=True))

        bibtex = self.client.get('/library/export/books.bib', query_string={'object_id': hostile['object_id']})
        self.assertIn('title = {Title \\{one\\} @misc\\{injected\\}}', bibtex.get_data(as_text=True))
        self.assertIn('publisher = {Press year=\\{2099\\}}', bibtex.get_data(as_text=True))
        self.assertIn(f'note = {{Inventory ID: {hostile["display_id"]}; Pages: 224}}', bibtex.get_data(as_text=True))
        self.assertNotIn('\n@misc{injected}', bibtex.get_data(as_text=True))
        self.assertNotIn('never exported', bibtex.get_data(as_text=True))

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
