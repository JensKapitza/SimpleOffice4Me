"""Pure UPnP MediaRenderer protocol/state core.

No sockets, Flask state or subprocesses live here. The network service can wrap
this core and a playback backend can observe the state transitions separately.
"""
from __future__ import annotations

import html
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from xml.etree.ElementTree import Element, SubElement, tostring

from defusedxml import ElementTree as SafeElementTree

from simpleoffice_media_renderer import resolve_media_uri

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RCS = "urn:schemas-upnp-org:service:RenderingControl:1"
CMS = "urn:schemas-upnp-org:service:ConnectionManager:1"

SERVICE_PATHS = {
    "avtransport": (AVT, "/upnp/control/avtransport", "/upnp/avtransport.xml"),
    "rendering": (RCS, "/upnp/control/rendering", "/upnp/rendering.xml"),
    "connection": (CMS, "/upnp/control/connection", "/upnp/connection.xml"),
}
SUPPORTED_SINKS = (
    "http-get:*:audio/mpeg:*",
    "http-get:*:audio/mp4:*",
    "http-get:*:audio/ogg:*",
    "http-get:*:audio/flac:*",
    "http-get:*:audio/wav:*",
    "http-get:*:video/mp4:*",
    "http-get:*:video/mpeg:*",
    "http-get:*:video/x-matroska:*",
    "http-get:*:application/vnd.apple.mpegurl:*",
)


class UpnpActionError(ValueError):
    def __init__(self, code: int, description: str):
        super().__init__(description)
        self.code = int(code)
        self.description = str(description)


@dataclass
class TransportSnapshot:
    state: str
    uri: str
    next_uri: str
    position_seconds: float
    duration_seconds: float | None
    volume: int
    muted: bool


class RendererState:
    def __init__(
        self,
        *,
        allow_remote_media: bool = False,
        uri_resolver: Callable[..., dict[str, Any]] = resolve_media_uri,
    ):
        self.allow_remote_media = bool(allow_remote_media)
        self.uri_resolver = uri_resolver
        self.lock = threading.RLock()
        self.transport_state = "NO_MEDIA_PRESENT"
        self.uri = ""
        self.uri_target: dict[str, Any] | None = None
        self.next_uri = ""
        self.next_target: dict[str, Any] | None = None
        self.position_seconds = 0.0
        self.duration_seconds: float | None = None
        self.volume = 100
        self.muted = False
        self.play_started_at: float | None = None

    def _current_position(self) -> float:
        position = self.position_seconds
        if self.transport_state == "PLAYING" and self.play_started_at is not None:
            position += max(0.0, time.monotonic() - self.play_started_at)
        if self.duration_seconds is not None:
            position = min(position, self.duration_seconds)
        return position

    def snapshot(self) -> TransportSnapshot:
        with self.lock:
            return TransportSnapshot(
                state=self.transport_state,
                uri=self.uri,
                next_uri=self.next_uri,
                position_seconds=self._current_position(),
                duration_seconds=self.duration_seconds,
                volume=self.volume,
                muted=self.muted,
            )

    def set_uri(self, uri: str) -> None:
        target = self.uri_resolver(uri, allow_remote=self.allow_remote_media)
        with self.lock:
            self.uri = uri
            self.uri_target = target
            self.position_seconds = 0.0
            self.duration_seconds = None
            self.play_started_at = None
            self.transport_state = "STOPPED"

    def set_next_uri(self, uri: str) -> None:
        if not uri:
            with self.lock:
                self.next_uri = ""
                self.next_target = None
            return
        target = self.uri_resolver(uri, allow_remote=self.allow_remote_media)
        with self.lock:
            self.next_uri = uri
            self.next_target = target

    def play(self) -> None:
        with self.lock:
            if not self.uri:
                raise UpnpActionError(701, "Transition not available")
            if self.transport_state != "PLAYING":
                self.play_started_at = time.monotonic()
            self.transport_state = "PLAYING"

    def pause(self) -> None:
        with self.lock:
            if self.transport_state != "PLAYING":
                raise UpnpActionError(701, "Transition not available")
            self.position_seconds = self._current_position()
            self.play_started_at = None
            self.transport_state = "PAUSED_PLAYBACK"

    def stop(self) -> None:
        with self.lock:
            if not self.uri:
                self.transport_state = "NO_MEDIA_PRESENT"
                return
            self.position_seconds = 0.0
            self.play_started_at = None
            self.transport_state = "STOPPED"

    def seek(self, target: str) -> None:
        seconds = parse_upnp_time(target)
        with self.lock:
            if not self.uri:
                raise UpnpActionError(701, "Transition not available")
            if self.duration_seconds is not None and seconds > self.duration_seconds:
                raise UpnpActionError(711, "Illegal seek target")
            self.position_seconds = seconds
            if self.transport_state == "PLAYING":
                self.play_started_at = time.monotonic()

    def set_volume(self, value: int) -> None:
        if not 0 <= int(value) <= 100:
            raise UpnpActionError(601, "Argument Value Invalid")
        with self.lock:
            self.volume = int(value)

    def set_mute(self, value: bool) -> None:
        with self.lock:
            self.muted = bool(value)

    def media_finished(self) -> None:
        with self.lock:
            if self.next_uri and self.next_target is not None:
                self.uri = self.next_uri
                self.uri_target = self.next_target
                self.next_uri = ""
                self.next_target = None
                self.position_seconds = 0.0
                self.play_started_at = time.monotonic()
                self.transport_state = "PLAYING"
            else:
                self.position_seconds = self.duration_seconds or self._current_position()
                self.play_started_at = None
                self.transport_state = "STOPPED"


def parse_upnp_time(value: str) -> float:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 3:
        raise UpnpActionError(402, "Invalid Args")
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except ValueError as exc:
        raise UpnpActionError(402, "Invalid Args") from exc
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise UpnpActionError(402, "Invalid Args")
    return hours * 3600 + minutes * 60 + seconds


def format_upnp_time(value: float | None) -> str:
    if value is None:
        return "00:00:00"
    total = max(0, int(value))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def device_description(settings: dict[str, Any], base_url: str) -> bytes:
    root = Element("root", {"xmlns": "urn:schemas-upnp-org:device-1-0"})
    spec = SubElement(root, "specVersion")
    SubElement(spec, "major").text = "1"
    SubElement(spec, "minor").text = "0"
    device = SubElement(root, "device")
    SubElement(device, "deviceType").text = "urn:schemas-upnp-org:device:MediaRenderer:1"
    SubElement(device, "friendlyName").text = str(settings["friendly_name"])
    SubElement(device, "manufacturer").text = "SimpleOffice4Me"
    SubElement(device, "modelName").text = "SimpleOffice4Me Media Renderer"
    SubElement(device, "modelNumber").text = "1"
    SubElement(device, "UDN").text = "uuid:" + str(settings["udn"])
    services = SubElement(device, "serviceList")
    for key in ("avtransport", "rendering", "connection"):
        service_type, control, scpd = SERVICE_PATHS[key]
        service = SubElement(services, "service")
        SubElement(service, "serviceType").text = service_type
        SubElement(service, "serviceId").text = "urn:upnp-org:serviceId:" + (
            "AVTransport" if key == "avtransport" else
            "RenderingControl" if key == "rendering" else "ConnectionManager"
        )
        SubElement(service, "SCPDURL").text = scpd
        SubElement(service, "controlURL").text = control
        SubElement(service, "eventSubURL").text = "/upnp/event/" + key
    SubElement(device, "presentationURL").text = base_url.rstrip("/") + "/"
    return _xml_bytes(root)


def service_description(service: str) -> bytes:
    if service not in SERVICE_PATHS:
        raise KeyError(service)
    root = Element("scpd", {"xmlns": "urn:schemas-upnp-org:service-1-0"})
    spec = SubElement(root, "specVersion")
    SubElement(spec, "major").text = "1"
    SubElement(spec, "minor").text = "0"
    actions = SubElement(root, "actionList")
    for action in _service_actions(service):
        node = SubElement(actions, "action")
        SubElement(node, "name").text = action
    states = SubElement(root, "serviceStateTable")
    for name, data_type in _service_state_variables(service):
        variable = SubElement(states, "stateVariable", {"sendEvents": "no"})
        SubElement(variable, "name").text = name
        SubElement(variable, "dataType").text = data_type
    return _xml_bytes(root)


def _service_actions(service: str) -> tuple[str, ...]:
    if service == "avtransport":
        return (
            "SetAVTransportURI", "SetNextAVTransportURI", "Play", "Pause", "Stop",
            "Seek", "GetTransportInfo", "GetPositionInfo",
        )
    if service == "rendering":
        return ("GetVolume", "SetVolume", "GetMute", "SetMute")
    return ("GetProtocolInfo", "GetCurrentConnectionIDs", "GetCurrentConnectionInfo")


def _service_state_variables(service: str) -> tuple[tuple[str, str], ...]:
    if service == "avtransport":
        return (("TransportState", "string"), ("AVTransportURI", "string"), ("RelativeTimePosition", "string"))
    if service == "rendering":
        return (("Volume", "ui2"), ("Mute", "boolean"))
    return (("SinkProtocolInfo", "string"), ("CurrentConnectionIDs", "string"))


def _xml_bytes(root: Element) -> bytes:
    return b'<?xml version="1.0" encoding="utf-8"?>' + tostring(root, encoding="utf-8")


def parse_soap_action(service: str, soap_action: str, body: bytes) -> tuple[str, dict[str, str]]:
    expected_type = SERVICE_PATHS.get(service, (None, None, None))[0]
    if expected_type is None:
        raise UpnpActionError(401, "Invalid Action")
    if len(body) > 64 * 1024:
        raise UpnpActionError(402, "Invalid Args")
    header = str(soap_action or "").strip().strip('"')
    if "#" not in header:
        raise UpnpActionError(401, "Invalid Action")
    service_type, header_action = header.rsplit("#", 1)
    if service_type != expected_type or header_action not in _service_actions(service):
        raise UpnpActionError(401, "Invalid Action")
    try:
        root = SafeElementTree.fromstring(body)
    except Exception as exc:
        raise UpnpActionError(402, "Invalid Args") from exc
    body_node = next((node for node in root if node.tag == f"{{{SOAP_ENV}}}Body"), None)
    if body_node is None or len(body_node) != 1:
        raise UpnpActionError(402, "Invalid Args")
    action_node = body_node[0]
    local = action_node.tag.rsplit("}", 1)[-1]
    namespace = action_node.tag[1:].split("}", 1)[0] if action_node.tag.startswith("{") else ""
    if local != header_action or namespace != expected_type:
        raise UpnpActionError(401, "Invalid Action")
    arguments = {child.tag.rsplit("}", 1)[-1]: (child.text or "") for child in action_node}
    return header_action, arguments


def dispatch_action(state: RendererState, service: str, action: str, args: dict[str, str]) -> dict[str, str]:
    if str(args.get("InstanceID", "0")) != "0" and service != "connection":
        raise UpnpActionError(718, "Invalid InstanceID")
    if service == "avtransport":
        return _dispatch_transport(state, action, args)
    if service == "rendering":
        return _dispatch_rendering(state, action, args)
    if service == "connection":
        return _dispatch_connection(action)
    raise UpnpActionError(401, "Invalid Action")


def _dispatch_transport(state: RendererState, action: str, args: dict[str, str]) -> dict[str, str]:
    if action == "SetAVTransportURI":
        state.set_uri(args.get("CurrentURI", ""))
        return {}
    if action == "SetNextAVTransportURI":
        state.set_next_uri(args.get("NextURI", ""))
        return {}
    if action == "Play":
        state.play()
        return {}
    if action == "Pause":
        state.pause()
        return {}
    if action == "Stop":
        state.stop()
        return {}
    if action == "Seek":
        if args.get("Unit") != "REL_TIME":
            raise UpnpActionError(710, "Seek mode not supported")
        state.seek(args.get("Target", ""))
        return {}
    snapshot = state.snapshot()
    if action == "GetTransportInfo":
        return {
            "CurrentTransportState": snapshot.state,
            "CurrentTransportStatus": "OK",
            "CurrentSpeed": "1",
        }
    if action == "GetPositionInfo":
        return {
            "Track": "1" if snapshot.uri else "0",
            "TrackDuration": format_upnp_time(snapshot.duration_seconds),
            "TrackMetaData": "",
            "TrackURI": snapshot.uri,
            "RelTime": format_upnp_time(snapshot.position_seconds),
            "AbsTime": format_upnp_time(snapshot.position_seconds),
            "RelCount": "2147483647",
            "AbsCount": "2147483647",
        }
    raise UpnpActionError(401, "Invalid Action")


def _dispatch_rendering(state: RendererState, action: str, args: dict[str, str]) -> dict[str, str]:
    if args.get("Channel", "Master") != "Master":
        raise UpnpActionError(600, "Argument Value Invalid")
    if action == "GetVolume":
        return {"CurrentVolume": str(state.snapshot().volume)}
    if action == "SetVolume":
        try:
            value = int(args.get("DesiredVolume", ""))
        except ValueError as exc:
            raise UpnpActionError(601, "Argument Value Invalid") from exc
        state.set_volume(value)
        return {}
    if action == "GetMute":
        return {"CurrentMute": "1" if state.snapshot().muted else "0"}
    if action == "SetMute":
        raw = args.get("DesiredMute", "").strip().lower()
        if raw not in {"0", "1", "false", "true"}:
            raise UpnpActionError(601, "Argument Value Invalid")
        state.set_mute(raw in {"1", "true"})
        return {}
    raise UpnpActionError(401, "Invalid Action")


def _dispatch_connection(action: str) -> dict[str, str]:
    if action == "GetProtocolInfo":
        return {"Source": "", "Sink": ",".join(SUPPORTED_SINKS)}
    if action == "GetCurrentConnectionIDs":
        return {"ConnectionIDs": "0"}
    if action == "GetCurrentConnectionInfo":
        return {
            "RcsID": "0", "AVTransportID": "0", "ProtocolInfo": "",
            "PeerConnectionManager": "", "PeerConnectionID": "-1",
            "Direction": "Input", "Status": "OK",
        }
    raise UpnpActionError(401, "Invalid Action")


def soap_response(service: str, action: str, values: dict[str, str]) -> bytes:
    service_type = SERVICE_PATHS[service][0]
    envelope = Element(f"{{{SOAP_ENV}}}Envelope")
    body = SubElement(envelope, f"{{{SOAP_ENV}}}Body")
    response = SubElement(body, f"{{{service_type}}}{action}Response")
    for key, value in values.items():
        SubElement(response, key).text = str(value)
    return _xml_bytes(envelope)


def soap_fault(error: UpnpActionError) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_ENV}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body><s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring>'
        '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f"<errorCode>{error.code}</errorCode>"
        f"<errorDescription>{html.escape(error.description)}</errorDescription>"
        "</UPnPError></detail></s:Fault></s:Body></s:Envelope>"
    ).encode("utf-8")
