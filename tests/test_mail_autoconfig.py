import unittest
from unittest.mock import patch

from app.mail_autoconfig import DiscoverySource, _fetch, discover_mail_settings, parse_thunderbird_config


XML = b'''<?xml version="1.0"?>
<clientConfig version="1.1">
  <emailProvider id="example.org">
    <domain>example.org</domain>
    <displayName>Example Mail</displayName>
    <incomingServer type="imap">
      <hostname>imap.example.org</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <username>%EMAILADDRESS%</username>
      <authentication>password-cleartext</authentication>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>smtp.example.org</hostname>
      <port>587</port>
      <socketType>STARTTLS</socketType>
      <username>%EMAILLOCALPART%</username>
      <authentication>password-cleartext</authentication>
    </outgoingServer>
  </emailProvider>
</clientConfig>'''


class MailAutoconfigTests(unittest.TestCase):
    def test_parses_thunderbird_provider_xml_and_expands_username_tokens(self):
        result = parse_thunderbird_config(XML, "alice@example.org", "thunderbird-ispdb")
        self.assertEqual("Example Mail", result["label"])
        self.assertEqual("imap.example.org", result["host"])
        self.assertEqual(993, result["port"])
        self.assertEqual("tls", result["security"])
        self.assertEqual("alice@example.org", result["username"])
        self.assertEqual("smtp.example.org", result["smtp_host"])
        self.assertEqual(587, result["smtp_port"])
        self.assertEqual("starttls", result["smtp_security"])
        self.assertEqual("alice", result["smtp_username"])
        self.assertEqual("Thunderbird ISPDB", result["source_label"])

    def test_oauth_configuration_produces_actionable_warning(self):
        xml = XML.replace(b"password-cleartext", b"OAuth2")
        result = parse_thunderbird_config(xml, "alice@example.org", "provider-autoconfig")
        self.assertTrue(any("OAuth2" in warning for warning in result["warnings"]))

    def test_invalid_email_is_rejected_before_network_access(self):
        with patch("app.mail_autoconfig._fetch") as fetch:
            with self.assertRaises(ValueError):
                discover_mail_settings("not-an-email")
            fetch.assert_not_called()

    def test_provider_autoconfig_is_preferred_over_ispdb(self):
        with patch("app.mail_autoconfig._fetch", return_value=XML) as fetch:
            result = discover_mail_settings("alice@example.org")
        self.assertEqual("provider-autoconfig", result["source"])
        self.assertEqual(1, fetch.call_count)

    def test_falls_back_to_domain_guess_when_no_provider_data_is_reachable(self):
        with patch("app.mail_autoconfig._fetch", side_effect=OSError("offline")):
            result = discover_mail_settings("alice@example.org")
        self.assertEqual("heuristic", result["source"])
        self.assertEqual("imap.example.org", result["host"])
        self.assertEqual("smtp.example.org", result["smtp_host"])
        self.assertEqual("guess", result["confidence"])
        self.assertTrue(result["warnings"])

    def test_provider_fetch_rejects_private_destination_before_http(self):
        source = DiscoverySource("provider-autoconfig", "https://autoconfig.example.org/mail/config-v1.1.xml", provider_owned=True)
        with patch("app.mail_autoconfig._host_is_public", return_value=False), patch(
            "app.mail_autoconfig.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(ValueError):
                _fetch(source)
            urlopen.assert_not_called()

    def test_plaintext_and_pop_only_configs_are_not_accepted(self):
        insecure = XML.replace(b"<socketType>SSL</socketType>", b"<socketType>plain</socketType>")
        with self.assertRaises(ValueError):
            parse_thunderbird_config(insecure, "alice@example.org", "provider-autoconfig")


if __name__ == "__main__":
    unittest.main()
