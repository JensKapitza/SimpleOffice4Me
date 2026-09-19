import unittest
from unittest.mock import Mock

from simpleoffice_media_upnp import (
    AVT,
    CMS,
    RCS,
    RendererState,
    UpnpActionError,
    device_description,
    dispatch_action,
    parse_soap_action,
    service_description,
    soap_fault,
    soap_response,
)


def soap(service_type: str, action: str, arguments: str = "") -> bytes:
    return (
        f'<?xml version="1.0"?>'
        f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<s:Body><u:{action} xmlns:u="{service_type}">{arguments}</u:{action}></s:Body>'
        f'</s:Envelope>'
    ).encode()


class UpnpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.resolver = Mock(
            return_value={
                "url": "http://media.local/movie.mp4",
                "scheme": "http",
                "hostname": "media.local",
                "port": 80,
                "addresses": ["192.168.10.20"],
                "path": "/movie.mp4",
            }
        )
        self.state = RendererState(uri_resolver=self.resolver)

    def test_device_description_exposes_media_renderer_services(self):
        payload = device_description(
            {
                "friendly_name": "Wohnzimmer",
                "udn": "11111111-1111-1111-1111-111111111111",
            },
            "http://192.168.10.3:8200",
        ).decode()
        self.assertIn("MediaRenderer:1", payload)
        self.assertIn("Wohnzimmer", payload)
        self.assertIn("AVTransport:1", payload)
        self.assertIn("RenderingControl:1", payload)
        self.assertIn("ConnectionManager:1", payload)

    def test_service_descriptions_expose_required_action_names(self):
        self.assertIn(b"SetAVTransportURI", service_description("avtransport"))
        self.assertIn(b"Seek", service_description("avtransport"))
        self.assertIn(b"SetVolume", service_description("rendering"))
        self.assertIn(b"GetProtocolInfo", service_description("connection"))

    def test_transport_uri_play_pause_seek_stop_and_next(self):
        dispatch_action(
            self.state,
            "avtransport",
            "SetAVTransportURI",
            {"InstanceID": "0", "CurrentURI": "http://media.local/movie.mp4"},
        )
        dispatch_action(self.state, "avtransport", "Play", {"InstanceID": "0"})
        self.assertEqual("PLAYING", self.state.snapshot().state)
        dispatch_action(self.state, "avtransport", "Pause", {"InstanceID": "0"})
        self.assertEqual("PAUSED_PLAYBACK", self.state.snapshot().state)
        dispatch_action(
            self.state,
            "avtransport",
            "Seek",
            {"InstanceID": "0", "Unit": "REL_TIME", "Target": "00:01:02"},
        )
        self.assertGreaterEqual(self.state.snapshot().position_seconds, 62)
        dispatch_action(
            self.state,
            "avtransport",
            "SetNextAVTransportURI",
            {"InstanceID": "0", "NextURI": "http://media.local/next.mp4"},
        )
        self.state.media_finished()
        self.assertEqual("PLAYING", self.state.snapshot().state)
        self.assertEqual("http://media.local/next.mp4", self.state.snapshot().uri)
        dispatch_action(self.state, "avtransport", "Stop", {"InstanceID": "0"})
        self.assertEqual("STOPPED", self.state.snapshot().state)

    def test_volume_and_mute_are_bounded(self):
        dispatch_action(
            self.state,
            "rendering",
            "SetVolume",
            {"InstanceID": "0", "Channel": "Master", "DesiredVolume": "37"},
        )
        dispatch_action(
            self.state,
            "rendering",
            "SetMute",
            {"InstanceID": "0", "Channel": "Master", "DesiredMute": "1"},
        )
        self.assertEqual(37, self.state.snapshot().volume)
        self.assertTrue(self.state.snapshot().muted)
        with self.assertRaises(UpnpActionError):
            dispatch_action(
                self.state,
                "rendering",
                "SetVolume",
                {"InstanceID": "0", "Channel": "Master", "DesiredVolume": "101"},
            )

    def test_connection_manager_advertises_audio_and_video_sinks(self):
        result = dispatch_action(self.state, "connection", "GetProtocolInfo", {})
        self.assertIn("audio/mpeg", result["Sink"])
        self.assertIn("video/mp4", result["Sink"])

    def test_soap_parser_requires_header_body_and_service_to_match(self):
        action, args = parse_soap_action(
            "avtransport",
            f'"{AVT}#Play"',
            soap(AVT, "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>"),
        )
        self.assertEqual("Play", action)
        self.assertEqual("0", args["InstanceID"])
        with self.assertRaises(UpnpActionError):
            parse_soap_action(
                "avtransport",
                f'"{AVT}#Play"',
                soap(RCS, "Play", "<InstanceID>0</InstanceID>"),
            )

    def test_soap_xml_entities_and_oversized_requests_fail_closed(self):
        entity = (
            b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
            + soap(AVT, "Play", "<InstanceID>&secret;</InstanceID>")
        )
        with self.assertRaises(UpnpActionError):
            parse_soap_action("avtransport", f'"{AVT}#Play"', entity)
        with self.assertRaises(UpnpActionError):
            parse_soap_action("connection", f'"{CMS}#GetProtocolInfo"', b"x" * (64 * 1024 + 1))

    def test_soap_responses_and_faults_do_not_reflect_exception_text(self):
        response = soap_response("rendering", "GetVolume", {"CurrentVolume": "44"})
        self.assertIn(b"GetVolumeResponse", response)
        self.assertIn(b"44", response)
        fault = soap_fault(UpnpActionError(401, "Invalid Action"))
        self.assertIn(b"<errorCode>401</errorCode>", fault)
        self.assertNotIn(b"Traceback", fault)


if __name__ == "__main__":
    unittest.main()
