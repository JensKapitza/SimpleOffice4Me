"""Regression tests for XML parsers that consume external data."""

import unittest
from pathlib import Path

from defusedxml.common import DefusedXmlException

from app.mail_autoconfig import parse_thunderbird_config


MAIL_AUTOCONFIG = Path("app/mail_autoconfig.py")


class XmlSecurityRegressionTests(unittest.TestCase):
    def test_mail_autoconfig_uses_defusedxml_parser(self):
        source = MAIL_AUTOCONFIG.read_text(encoding="utf-8")
        self.assertIn("safe_xml_fromstring", source)
        self.assertNotIn("ET.fromstring(data)", source)

    def test_mail_autoconfig_rejects_doctype_and_external_entities(self):
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

        with self.assertRaises(DefusedXmlException):
            parse_thunderbird_config(payload, "user@example.org", "test")


if __name__ == "__main__":
    unittest.main()
