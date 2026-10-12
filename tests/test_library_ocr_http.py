"""Uploaded-image contracts through the public, CSRF-protected library API."""
import io
import concurrent.futures
import os
import re
import struct
import tempfile
import unittest
import zlib
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from app import app
from app import db as database


class LibraryOcrHttpContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.root = root
        saved = {key: app.config.get(key) for key in
                 ('TESTING', 'DATABASE', 'DOCUMENT_ROOT', 'TEST_CSRF_PROTECTION')}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(TESTING=True, DATABASE=str(root / 'users.sqlite'),
                          DOCUMENT_ROOT=str(root / 'documents'), TEST_CSRF_PROTECTION=True)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('ocr-member','unused',0)")
            db.commit()
            identifier = db.execute("SELECT id FROM user WHERE username='ocr-member'").fetchone()[0]
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = identifier
        page = self.client.get('/library')
        self.assertEqual(200, page.status_code)
        self.token = re.search(r'name="_csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        output = io.BytesIO()
        Image.new('RGB', (16, 8), 'white').save(output, format='PNG')
        self.image = output.getvalue()

    def post(self, path, data, *, token=True, page='1'):
        form = {'image': (io.BytesIO(data), '../../book.png'), 'straighten': '0', 'brighten': '0', 'page': page}
        if token:
            form['_csrf_token'] = self.token
        return self.client.post(path, data=form)

    def test_authenticated_image_returns_png_preview_with_known_dimensions(self):
        """A 16x8 PNG yields a 16x8 PNG preview without turning its filename into a path."""
        result = self.post('/library/ocr/optimize', self.image)
        self.assertEqual(200, result.status_code)
        self.assertEqual('image/png', result.mimetype)
        self.assertEqual('no-store', result.headers['Cache-Control'])
        with Image.open(io.BytesIO(result.data)) as preview:
            self.assertEqual((16, 8), preview.size)
            self.assertEqual('PNG', preview.format)

    def test_missing_csrf_cannot_invoke_either_image_endpoint(self):
        """Valid images still require CSRF for both preview and recognition."""
        for path in ('/library/ocr/optimize', '/library/ocr/analyze'):
            with self.subTest(path=path):
                result = self.post(path, self.image, token=False)
                self.assertEqual(403, result.status_code)

    def test_invalid_page_unsupported_data_oversize_and_bomb_return_400(self):
        """Invalid uploads/page numbers are API input errors, never application crashes."""
        bomb = bytearray(self.image)
        struct.pack_into('>II', bomb, 16, 100000, 100000)
        struct.pack_into('>I', bomb, 29, zlib.crc32(bomb[12:29]))
        cases = ((self.image, '0'), (b'not an image', '1'),
                 (b'x' * 12582913, '1'), (bytes(bomb), '1'))
        for path in ('/library/ocr/optimize', '/library/ocr/analyze'):
            for payload, page in cases:
                with self.subTest(path=path, size=len(payload), page=page):
                    result = self.post(path, payload, page=page)
                    self.assertEqual(400, result.status_code)
                    self.assertNotIn(b'Traceback', result.data)

    def test_large_valid_image_has_bounded_preview_dimensions(self):
        """A 3200x800 photo becomes a 2000x500 preview, preserving its documented aspect ratio."""
        data = io.BytesIO()
        Image.new('RGB', (3200, 800), 'white').save(data, format='PNG')
        result = self.post('/library/ocr/optimize', data.getvalue())
        self.assertEqual(200, result.status_code)
        with Image.open(io.BytesIO(result.data)) as image:
            self.assertEqual((2000, 500), image.size)

    @unittest.skipIf(os.name == 'nt', 'POSIX executable fixture')
    def test_busy_ocr_rejects_concurrent_work_and_recognition_does_not_create_books(self):
        """A running local OCR blocks another job with 503; recognized text is never auto-persisted."""
        binary = self.root / 'tesseract'
        binary.write_text('#!' + os.sys.executable + '\n' + '''
import os, time
from pathlib import Path
root = Path(os.environ['OCR_FIXTURE_ROOT'])
(root / 'started').touch()
while not (root / 'release').exists(): time.sleep(.02)
print('UnstoredRecognitionMarker')
''', encoding='utf-8')
        binary.chmod(0o700)
        self.assertFalse(self.client.get('/library/find?q=UnstoredRecognitionMarker').json['found'])
        with patch.dict(os.environ, {'PATH': str(self.root) + os.pathsep + os.environ.get('PATH', ''),
                                     'OCR_FIXTURE_ROOT': str(self.root)}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(self.post, '/library/ocr/analyze', self.image)
                try:
                    deadline = time.monotonic() + 10
                    while not (self.root / 'started').exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue((self.root / 'started').exists())
                    other = app.test_client()
                    cookie_name = app.config["SESSION_COOKIE_NAME"]
                    other.set_cookie(cookie_name, self.client.get_cookie(cookie_name).value)
                    result = other.post('/library/ocr/optimize', data={
                        'image': (io.BytesIO(self.image), 'next.png'), '_csrf_token': self.token})
                    self.assertEqual(503, result.status_code)
                finally:
                    (self.root / 'release').touch()
                result = running.result(timeout=10)
        self.assertEqual(200, result.status_code)
        self.assertEqual('UnstoredRecognitionMarker', result.json['text'])
        self.assertEqual('no-store', result.headers['Cache-Control'])
        self.assertFalse(self.client.get('/library/find?q=UnstoredRecognitionMarker').json['found'])
