import http.client
import socket
import threading
import unittest
from unittest.mock import Mock

from simpleoffice_media_service import MediaRendererService, SsdpAdvertiser
from simpleoffice_media_upnp import AVT, RendererState


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MediaRendererNetworkServiceTests(unittest.TestCase):
    def settings(self, port):
        return {
            "enabled": True,
            "friendly_name": "Test Renderer",
            "bind": "127.0.0.1",
            "port": port,
            "allow_remote_media": False,
            "audio_output": "default",
            "video_mode": "window",
            "udn": "11111111-1111-1111-1111-111111111111",
        }

    def test_http_device_and_soap_control_are_local_and_bounded(self):
        port = free_port()
        resolver = Mock(
            return_value={
                "url": "http://media.local/movie.mp4",
                "scheme": "http",
                "hostname": "media.local",
                "port": 80,
                "addresses": ["192.168.1.10"],
                "path": "/movie.mp4",
            }
        )
        state = RendererState(uri_resolver=resolver)
        service = MediaRendererService(self.settings(port), state=state)
        service.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/upnp/device.xml")
            response = connection.getresponse()
            payload = response.read()
            self.assertEqual(200, response.status)
            self.assertIn(b"MediaRenderer:1", payload)
            self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
            connection.close()

            body = (
                f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                f'<s:Body><u:SetAVTransportURI xmlns:u="{AVT}">'
                f'<InstanceID>0</InstanceID><CurrentURI>http://media.local/movie.mp4</CurrentURI>'
                f'<CurrentURIMetaData></CurrentURIMetaData></u:SetAVTransportURI></s:Body></s:Envelope>'
            ).encode()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request(
                "POST",
                "/upnp/control/avtransport",
                body=body,
                headers={
                    "Content-Type": 'text/xml; charset="utf-8"',
                    "SOAPAction": f'"{AVT}#SetAVTransportURI"',
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            response.read()
            connection.close()
            self.assertEqual("STOPPED", state.snapshot().state)
            self.assertEqual("http://media.local/movie.mp4", state.snapshot().uri)

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request(
                "POST",
                "/upnp/control/avtransport",
                body=b"x",
                headers={"SOAPAction": f'"{AVT}#Play"', "Content-Length": str(64 * 1024 + 1)},
            )
            response = connection.getresponse()
            self.assertEqual(413, response.status)
            response.read()
            connection.close()
        finally:
            service.stop()
        self.assertFalse(service.is_alive())

    def test_ssdp_search_only_replies_to_private_sources_and_known_targets(self):
        settings = {
            "bind": "192.168.50.10",
            "port": 8200,
            "udn": "11111111-1111-1111-1111-111111111111",
        }
        advertiser = SsdpAdvertiser(settings, threading.Event())
        advertiser.socket = Mock()
        request = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 1\r\n"
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n"
        ).encode()
        advertiser._handle_search(request, ("192.168.50.20", 1901))
        self.assertEqual(1, advertiser.socket.sendto.call_count)
        payload, peer = advertiser.socket.sendto.call_args.args
        self.assertEqual(("192.168.50.20", 1901), peer)
        self.assertIn(b"MediaRenderer:1", payload)

        advertiser.socket.reset_mock()
        advertiser._handle_search(request, ("8.8.8.8", 1901))
        advertiser._handle_search(request.replace(b"MediaRenderer:1", b"Unknown:1"), ("192.168.50.21", 1901))
        advertiser.socket.sendto.assert_not_called()


if __name__ == "__main__":
    unittest.main()
