from __future__ import annotations

import unittest
import urllib.error
import urllib.request

from app.fritzbox_contacts import (
    DEFAULT_CONTROL_URL,
    DEFAULT_SERVICE,
    FritzBoxClient,
    FritzBoxError,
)


PHONEBOOK_SERVICE_XML = b"""<?xml version='1.0'?>
<root xmlns='urn:dslforum-org:device-1-0'>
  <device>
    <serviceList>
      <service>
        <serviceType>urn:dslforum-org:service:X_AVM-DE_OnTel:1</serviceType>
        <serviceId>urn:X_AVM-DE_OnTel-com:serviceId:X_AVM-DE_OnTel1</serviceId>
        <controlURL>/upnp/control/x_contact</controlURL>
        <eventSubURL>/upnp/control/x_contact</eventSubURL>
        <SCPDURL>/x_contactSCPD.xml</SCPDURL>
      </service>
    </serviceList>
  </device>
</root>
"""

PHONEBOOK_LIST_SOAP = b"""<?xml version='1.0'?>
<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/'>
  <s:Body>
    <u:GetPhonebookListResponse xmlns:u='urn:dslforum-org:service:X_AVM-DE_OnTel:1'>
      <NewPhonebookList>0,1</NewPhonebookList>
    </u:GetPhonebookListResponse>
  </s:Body>
</s:Envelope>
"""


def bare_client() -> FritzBoxClient:
    client = FritzBoxClient.__new__(FritzBoxClient)
    client.base_url = "https://fritz.box"
    client.username = "test"
    client.password = "test"
    client.verify_tls = False
    client.timeout = 8.0
    client.service_type = DEFAULT_SERVICE
    client.control_url = DEFAULT_CONTROL_URL
    return client


class FritzBoxTr064PathTests(unittest.TestCase):
    def test_local_https_description_keeps_plain_control_path(self):
        client = bare_client()
        calls: list[str] = []

        def fake_read(url):
            calls.append(str(url))
            return PHONEBOOK_SERVICE_XML

        client._read = fake_read  # type: ignore[method-assign]
        client._discover()

        self.assertEqual(calls, ["https://fritz.box/tr64desc.xml"])
        self.assertEqual(client.control_url, "/upnp/control/x_contact")

    def test_remote_https_description_adds_tr064_prefix(self):
        client = bare_client()
        calls: list[str] = []

        def fake_read(url):
            value = str(url)
            calls.append(value)
            if value.endswith("/tr64desc.xml") and "/tr064/" not in value:
                raise FritzBoxError("FRITZ!Box antwortet mit HTTP 404")
            return PHONEBOOK_SERVICE_XML

        client._read = fake_read  # type: ignore[method-assign]
        client._discover()

        self.assertEqual(
            calls,
            ["https://fritz.box/tr64desc.xml", "https://fritz.box/tr064/tr64desc.xml"],
        )
        self.assertEqual(client.control_url, "/tr064/upnp/control/x_contact")

    def test_control_candidates_cover_local_and_remote_paths(self):
        client = bare_client()
        self.assertEqual(
            client._control_candidates(),
            ["/upnp/control/x_contact", "/tr064/upnp/control/x_contact"],
        )
        client.control_url = "/tr064/upnp/control/x_contact"
        self.assertEqual(
            client._control_candidates(),
            ["/tr064/upnp/control/x_contact", "/upnp/control/x_contact"],
        )

    def test_soap_retries_remote_prefix_after_plain_path_404(self):
        client = bare_client()
        calls: list[str] = []

        def fake_read(request):
            self.assertIsInstance(request, urllib.request.Request)
            calls.append(request.full_url)
            if request.full_url.endswith("/upnp/control/x_contact") and "/tr064/" not in request.full_url:
                cause = urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)
                try:
                    raise cause
                except urllib.error.HTTPError as exc:
                    raise FritzBoxError("FRITZ!Box antwortet mit HTTP 404") from exc
            return PHONEBOOK_LIST_SOAP

        client._read = fake_read  # type: ignore[method-assign]
        result = client.soap("GetPhonebookList")

        self.assertEqual(result["NewPhonebookList"], "0,1")
        self.assertEqual(
            calls,
            [
                "https://fritz.box/upnp/control/x_contact",
                "https://fritz.box/tr064/upnp/control/x_contact",
            ],
        )
        self.assertEqual(client.control_url, "/tr064/upnp/control/x_contact")


if __name__ == "__main__":
    unittest.main()
