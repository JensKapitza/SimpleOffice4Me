"""Small S3 XML response helpers."""
from __future__ import annotations

from xml.sax.saxutils import escape


NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def element(name: str, value: object) -> str:
    return f"<{name}>{escape(str(value))}</{name}>"


def document(name: str, body: str = "") -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<{name} xmlns="{NS}">{body}</{name}>'


def error(code: str, message: str, request_id: str) -> str:
    return document("Error", element("Code", code) + element("Message", message) + element("RequestId", request_id) + element("HostId", request_id))
