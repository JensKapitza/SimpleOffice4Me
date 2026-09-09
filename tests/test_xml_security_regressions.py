"""Regression tests for XML parsers that consume external data."""

from pathlib import Path

import pytest
from defusedxml.common import DefusedXmlException

from app.mail_autoconfig import parse_thunderbird_config


MAIL_AUTOCONFIG = Path("app/mail_autoconfig.py")


def test_mail_autoconfig_uses_defusedxml_parser():
    source = MAIL_AUTOCONFIG.read_text(encoding="utf-8")
    assert "safe_xml_fromstring" in source
    assert "ET.fromstring(data)" not in source


def test_mail_autoconfig_rejects_doctype_and_external_entities():
    payload = b'''<?xml version="1.0"?>
<!DOCTYPE clientConfig [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<clientConfig>
  <emailProvider id="example.org">
    <displayName>&xxe;</displayName>
    <incomingServer type="imap">
      <hostname>imap.example.org</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <username>%EMAILADDRESS%</username>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>smtp.example.org</hostname>
      <port>587</port>
      <socketType>STARTTLS</socketType>
      <username>%EMAILADDRESS%</username>
    </outgoingServer>
  </emailProvider>
</clientConfig>'''

    with pytest.raises(DefusedXmlException):
        parse_thunderbird_config(payload, "user@example.org", "test")
