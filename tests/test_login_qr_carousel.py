"""Public login-page URL contracts; DNS is the only simulated I/O boundary."""
import base64
import os
import socket
from html.parser import HTMLParser
from unittest.mock import patch
from xml.etree import ElementTree

from app import app
from account_api_fixture import AccountApiFixture


class VisibleLoginLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.images = []
        self.href = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a':
            self.href = values.get('href', '')
            self.text = []
        if tag == 'img' and values.get('alt', '').startswith('QR-Code für '):
            self.images.append(values.get('src', ''))

    def handle_data(self, value):
        if self.href is not None:
            self.text.append(value)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href is not None:
            if self.href.startswith(('https://', 'http://')) and ''.join(self.text).strip() == self.href:
                self.links.append(self.href)
            self.href = None


class LoginQRCodeTests(AccountApiFixture):
    def page(self, base='http://127.0.0.1:8080', peer='127.0.0.1', *, headers=None, variables=None, mount=''):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.22', 0)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.9', 0))]
        variables = {'SIMPLEOFFICE_LOGIN_QR': '1', 'SIMPLEOFFICE_LOGIN_QR_LAN': '1',
                     'SIMPLEOFFICE_LOGIN_QR_PORTS': '', 'SIMPLEOFFICE_LOGIN_QR_URLS': '', **(variables or {})}
        with patch.dict(os.environ, variables), patch('socket.getaddrinfo', return_value=addresses):
            response = app.test_client().get('/auth/login', base_url=base, headers=headers,
                environ_overrides={'REMOTE_ADDR': peer, 'SCRIPT_NAME': mount})
        self.assertEqual(200, response.status_code)
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))
        parser = VisibleLoginLinks()
        parser.feed(response.get_data(as_text=True))
        return response, parser

    def test_direct_local_client_sees_current_and_private_candidate_with_real_svg(self):
        _, page = self.page()
        self.assertEqual(['http://127.0.0.1:8080/auth/login',
                          'http://192.168.1.22:8080/auth/login'], page.links)
        self.assertEqual(2, len(page.images))
        for source in page.images:
            self.assertTrue(source.startswith('data:image/svg+xml;base64,'))
            root = ElementTree.fromstring(base64.b64decode(source.split(',', 1)[1]))
            self.assertEqual('{http://www.w3.org/2000/svg}svg', root.tag)

    def test_localhost_is_a_local_candidate_source(self):
        _, page = self.page(base='http://localhost:8080')
        self.assertEqual(['http://localhost:8080/auth/login',
                          'http://192.168.1.22:8080/auth/login'], page.links)

    def test_external_client_and_public_reverse_proxy_cannot_see_private_candidates(self):
        for base, peer in [('https://office.example', '198.51.100.27'),
                           ('https://office.example', '127.0.0.1'),
                           ('http://192.168.1.22:8080', '198.51.100.27')]:
            with self.subTest(base=base, peer=peer):
                _, page = self.page(base, peer)
                self.assertEqual([base + '/auth/login'], page.links)

    def test_forwarded_headers_suppress_private_addresses_even_on_a_local_host(self):
        for header in ('Forwarded', 'X-Forwarded-For', 'X-Real-IP'):
            _, page = self.page(headers={header: '203.0.113.10'})
            self.assertEqual(['http://127.0.0.1:8080/auth/login'], page.links)

    def test_configured_proxy_without_forwarded_headers_cannot_disclose_private_candidates(self):
        from werkzeug.middleware.proxy_fix import ProxyFix
        deployed = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
        with patch.object(app, 'wsgi_app', deployed):
            _, page = self.page()
        self.assertEqual(['http://127.0.0.1:8080/auth/login'], page.links)

    def test_disable_switches_keep_authentication_available(self):
        response, page = self.page(variables={'SIMPLEOFFICE_LOGIN_QR': '0'})
        self.assertEqual([], page.links)
        self.assertIn(b'name="_csrf_token"', response.data)
        _, page = self.page(variables={'SIMPLEOFFICE_LOGIN_QR_LAN': '0'})
        self.assertEqual(['http://127.0.0.1:8080/auth/login'], page.links)

    def test_mount_prefix_is_preserved(self):
        _, page = self.page(mount='/office')
        self.assertEqual(['http://127.0.0.1:8080/office/auth/login',
                          'http://192.168.1.22:8080/office/auth/login'], page.links)

    def test_explicit_ports_are_validated_and_duplicate_addresses_are_removed(self):
        _, page = self.page(variables={'SIMPLEOFFICE_LOGIN_QR_PORTS': '8080,8443,8443,0,65536,invalid'})
        self.assertEqual(['http://127.0.0.1:8080/auth/login', 'http://192.168.1.22:8080/auth/login',
                          'http://192.168.1.22:8443/auth/login'], page.links)

    def test_configured_bases_support_ipv6_and_ignore_credential_or_parameter_urls(self):
        _, page = self.page(variables={'SIMPLEOFFICE_LOGIN_QR_URLS':
            'https://[fd00::1]:8443/,https://office.example/tenant/,ftp://bad.example/,'
            'https://user:fake-password@bad.example/,https://bad.example/?token=fake-marker,'
            'https://bad.example:99999/,https://office.example/tenant/auth/login'})
        self.assertEqual(['http://127.0.0.1:8080/auth/login', 'http://192.168.1.22:8080/auth/login',
                          'https://[fd00::1]:8443/auth/login',
                          'https://office.example/tenant/auth/login'], page.links)

    def test_candidates_are_bounded_to_twenty_visible_links(self):
        _, page = self.page(variables={'SIMPLEOFFICE_LOGIN_QR_PORTS': ','.join(str(p) for p in range(8100, 8200))})
        self.assertEqual(20, len(page.links))
        self.assertEqual('http://192.168.1.22:8117/auth/login', page.links[-1])

    def test_opening_login_links_does_not_grant_authenticated_access(self):
        guest = app.test_client()
        response = guest.get('/auth/login?password=fake-marker')
        parser = VisibleLoginLinks(); parser.feed(response.get_data(as_text=True))
        self.assertTrue(parser.links)
        self.assertTrue(all('fake-marker' not in link for link in parser.links))
        self.assertEqual('/auth/login', guest.get('/auth/profile').headers['Location'])
