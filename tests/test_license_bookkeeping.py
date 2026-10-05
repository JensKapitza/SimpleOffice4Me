"""Bookkeeping and business-document lifecycle requests share document metering."""
import os
import unittest
from unittest.mock import patch

from app import app
from app.license_metering import LicenseStore, feature_for_endpoint
import test_first_run_setup as _fixture


class BookkeepingLicenseTests(unittest.TestCase):
    setUp = _fixture.FirstRunSetupTest.setUp
    tearDown = _fixture.FirstRunSetupTest.tearDown

    def test_registered_bookkeeping_and_lifecycle_routes_use_documents(self):
        routes = [rule for rule in app.url_map.iter_rules()
                  if rule.endpoint.startswith(('contact_audit.euer.', 'v3_finance.'))]
        self.assertGreater(len(routes), 20)
        for rule in routes:
            with self.subTest(endpoint=rule.endpoint):
                self.assertEqual('documents', feature_for_endpoint(rule.endpoint, rule.rule))

    def test_aliases_and_namespace_boundaries(self):
        for endpoint in ('euer.overview', 'finance.index', 'v3_finance.index',
                         'contact_audit.euer.finance.index'):
            self.assertEqual('documents', feature_for_endpoint(endpoint))
        self.assertEqual('contacts', feature_for_endpoint('contact_audit.index'))
        self.assertEqual('contacts', feature_for_endpoint('contact_audit.euer_export'))
        self.assertEqual('', feature_for_endpoint('finance_admin.index'))
        self.assertEqual('', feature_for_endpoint('admin.index', '/documents/business/bookkeeping'))

    def test_real_pages_count_once_as_documents_without_contacts(self):
        with patch.dict(os.environ, {'SIMPLEOFFICE_V3_FINANCE_ENABLED': '1'}):
            for path in ('/documents/business/bookkeeping',
                         '/documents/business/bookkeeping/finances',
                         '/documents/business/lifecycle-v3'):
                response = self.client.get(path, base_url=self.base_url)
                self.assertEqual(200, response.status_code, path)
        overview = LicenseStore(self.root).overview()
        self.assertEqual({'users': 1, 'requests': 3}, overview['usage']['documents'])
        self.assertEqual({'users': 0, 'requests': 0}, overview['usage']['contacts'])
        self.assertEqual(1, overview['active_users'])

    def test_document_fallback_counts_once_but_errors_and_anonymous_do_not(self):
        with patch.dict(os.environ, {'SIMPLEOFFICE_V3_FINANCE_ENABLED': '0'}):
            response = self.client.get('/documents/business/lifecycle-v3', base_url=self.base_url)
            self.assertEqual(302, response.status_code)
            self.assertTrue(response.headers['Location'].endswith('/documents/business/invoices'))
        with patch.dict(os.environ, {'SIMPLEOFFICE_V3_FINANCE_ENABLED': '1'}):
            response = self.client.get('/documents/business/lifecycle-v3/missing', base_url=self.base_url)
            self.assertEqual(404, response.status_code)
        client = app.test_client()
        response = client.get('/documents/business/bookkeeping', base_url=self.base_url)
        self.assertEqual(302, response.status_code)
        overview = LicenseStore(self.root).overview()
        self.assertEqual({'users': 1, 'requests': 1}, overview['usage']['documents'])
        self.assertEqual({'users': 0, 'requests': 0}, overview['usage']['contacts'])
