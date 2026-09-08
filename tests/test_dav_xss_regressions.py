"""Regression tests for reflected-XSS boundaries in DAV XML responses."""

from __future__ import annotations

from app.caldav import _multistatus
from app.carddav import _xml
from app.webdav import _need_privileges_response, _prop_response


ATTACK = '<script>alert("xss")</script>'
ESCAPED = '&lt;script&gt;alert("xss")&lt;/script&gt;'


def _assert_xml_boundary(response) -> None:
    assert response.mimetype == "application/xml"
    body = response.get_data(as_text=True)
    assert ATTACK not in body


def test_caldav_multistatus_escapes_untrusted_href(app):
    with app.test_request_context("/caldav/"):
        response = _multistatus([(f"/caldav/{ATTACK}", "<d:displayname>safe</d:displayname>", "HTTP/1.1 200 OK")])
    _assert_xml_boundary(response)
    assert ESCAPED in response.get_data(as_text=True)


def test_carddav_multistatus_escapes_untrusted_href(app):
    with app.test_request_context("/carddav/"):
        response = _xml([(f"/carddav/{ATTACK}", "<d:displayname>safe</d:displayname>")])
    _assert_xml_boundary(response)
    assert ESCAPED in response.get_data(as_text=True)


def test_webdav_privilege_error_escapes_untrusted_href(app):
    with app.test_request_context("/webdav/"):
        response = _need_privileges_response(f"/webdav/{ATTACK}", "write", "GET")
    _assert_xml_boundary(response)
    assert ESCAPED in response.get_data(as_text=True)


def test_webdav_prop_response_escapes_href_and_display_name(app):
    with app.test_request_context("/webdav/"):
        xml = _prop_response(f"/webdav/{ATTACK}", ATTACK)
    assert ATTACK not in xml
    assert ESCAPED in xml
