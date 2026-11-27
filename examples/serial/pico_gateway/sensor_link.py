"""Minimal read-only HTTP link for a configured private sensor lab."""
import json
import socket
import time

TIMEOUT_MS = 650
MAX_HEADER = 1024
MAX_BODY = 512
STATUS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 503: "Unavailable"}


def now_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.monotonic() * 1000)


def elapsed_ms(start):
    current = now_ms()
    return time.ticks_diff(current, start) if hasattr(time, "ticks_diff") else current - start


def local_ipv4(ip):
    if not isinstance(ip, str) or len(ip) > 15:
        raise ValueError("Expected a fixed private IPv4 address")
    parts = ip.split(".")
    if len(parts) != 4 or any(not part.isdigit() for part in parts):
        raise ValueError("Expected a fixed private IPv4 address")
    a, b, c, d = [int(part) for part in parts]
    if ip != "%d.%d.%d.%d" % (a, b, c, d) or max(a, b, c, d) > 255:
        raise ValueError("Expected a canonical IPv4 address")
    if not (a in (10, 127) or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)):
        raise ValueError("Use this kit only in a private sensor LAN")
    return ip


def receive(sock, size, start):
    remaining = TIMEOUT_MS - elapsed_ms(start)
    if remaining <= 0:
        raise OSError("Sensor read timed out")
    sock.settimeout(remaining / 1000)
    return sock.recv(size)


def headers(sock, start):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = receive(sock, min(256, MAX_HEADER - len(data)), start)
        if not chunk:
            raise ValueError("Incomplete HTTP headers")
        data += chunk
        if len(data) >= MAX_HEADER and b"\r\n\r\n" not in data:
            raise ValueError("HTTP headers too large")
    return data.split(b"\r\n\r\n", 1)


def send_all(sock, data):
    start = now_ms()
    while data:
        remaining = TIMEOUT_MS - elapsed_ms(start)
        if remaining <= 0:
            raise OSError("Sensor send timed out")
        sock.settimeout(remaining / 1000)
        size = sock.send(data)
        if not size:
            raise OSError("Sensor socket closed")
        data = data[size:]


def _value(value):
    if type(value) not in (int, float) or not -1e12 <= value <= 1e12:
        raise ValueError("Invalid sensor value")
    return value


def read_sensor(address, expected):
    ip, port = address
    sock = socket.socket()
    try:
        sock.settimeout(TIMEOUT_MS / 1000)
        sock.connect((ip, port))
        send_all(sock, ("GET /reading HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % ip).encode())
        start = now_ms()
        head, body = headers(sock, start)
        lines = head.split(b"\r\n")
        if not lines[0].startswith(b"HTTP/1.1 200 "):
            raise ValueError("Sensor not available")
        lengths = [line[15:] for line in lines[1:] if line.lower().startswith(b"content-length:")]
        if len(lengths) != 1:
            raise ValueError("Invalid content length")
        size = int(lengths[0])
        if not 0 < size <= MAX_BODY or len(body) > size:
            raise ValueError("Invalid sensor payload size")
        while len(body) < size:
            chunk = receive(sock, size - len(body), start)
            if not chunk:
                raise ValueError("Incomplete sensor payload")
            body += chunk
        payload = json.loads(body.decode())
        if type(payload) is not dict or payload.get("id") != expected:
            raise ValueError("Wrong sensor ID")
        return _value(payload.get("value"))
    finally:
        sock.close()


def _node_id(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 24 or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in name
    ):
        raise ValueError("Use a sensor ID such as room_a")
    return name


def sensor_handler(name, measure):
    _node_id(name)

    def handler(path):
        if path != b"/reading":
            return 404, {}
        try:
            return 200, {"id": name, "value": _value(measure())}
        except (ValueError, OSError):
            return 503, {"online": False}

    return handler


def gateway_handler(sources):
    if not 1 <= len(sources) <= 2:
        raise ValueError("Configure one or two sensors")
    nodes = []
    for name, ip, port in sources:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid sensor port")
        nodes.append((_node_id(name), (local_ipv4(ip), port)))
    if len({name for name, _ in nodes}) != len(nodes):
        raise ValueError("Sensor IDs must be unique")

    def handler(path):
        if path != b"/status":
            return 404, {}
        result = {}
        for name, address in nodes:
            try:
                result[name] = {"online": True, "value": read_sensor(address, name)}
            except (ValueError, OSError):
                result[name] = {"online": False}
        return 200, {"nodes": result}

    return handler


def handle_client(sock, from_ip, allowed_ip, handler):
    try:
        if from_ip != allowed_ip:
            code, payload = 403, {}
        else:
            head, _ = headers(sock, now_ms())
            request = head.split(b"\r\n", 1)[0].split(b" ")
            if len(request) != 3 or request[0] != b"GET" or request[2] not in (b"HTTP/1.0", b"HTTP/1.1"):
                code, payload = 400, {}
            else:
                code, payload = handler(request[1])
        body = json.dumps(payload).encode()
        header = ("HTTP/1.1 %d %s\r\nContent-Type: application/json\r\n"
                  "Cache-Control: no-store\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                  % (code, STATUS[code], len(body))).encode()
        send_all(sock, header + body)
    except (ValueError, OSError):
        # A malformed or disconnected caller must not stop the next request.
        return
    finally:
        sock.close()


def listen(ip, port):
    server = socket.socket()
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((local_ipv4(ip), port))
        server.listen(2)
        return server
    except (OSError, ValueError):
        server.close()
        raise


def serve(ip, port, allowed_ip, handler):
    allowed_ip = local_ipv4(allowed_ip)
    server = listen(ip, port)
    try:
        while True:
            client, peer = server.accept()
            handle_client(client, peer[0], allowed_ip, handler)
    finally:
        server.close()
