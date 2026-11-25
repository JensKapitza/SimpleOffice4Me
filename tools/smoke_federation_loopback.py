"""Opt-in HTTPS federation acceptance with two synthetic localhost processes."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from hashlib import sha256
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPSHandler, HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
ADMIN = "/admin/federation"
DISCOVERY = ADMIN + "/peer-discovery"
PEERS = ("loop-office-a", "loop-office-b")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def make_certificates(directory):
    def openssl(*args):
        subprocess.run(["openssl", *args], cwd=directory, check=True, timeout=20, capture_output=True)

    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.crt",
            "-days", "2", "-subj", "/CN=Disposable SimpleOffice Test CA",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-addext", "subjectKeyIdentifier=hash")
    (directory / "server.ext").write_text("basicConstraints=critical,CA:FALSE\n"
                                         "keyUsage=digitalSignature,keyEncipherment\n"
                                         "extendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\n", encoding="ascii")
    for serial, name in enumerate(PEERS, 100):
        openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key",
                "-out", f"{name}.req", "-subj", f"/CN={name}")
        openssl("x509", "-req", "-in", f"{name}.req", "-CA", "ca.crt", "-CAkey", "ca.key",
                "-set_serial", str(serial), "-out", f"{name}.crt", "-days", "2", "-extfile", "server.ext")


def serve(root, name, port, tls):
    from werkzeug.serving import make_server

    sys.path.insert(0, str(root / "checkout"))
    from app import app, db, federation_local_identity
    from app.password_security import hash_password
    from app.settings_store import SettingsStore

    def observed(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith("/federation/"):
            # Record evidence of ordinary protocol calls, never header values.
            with (root / "calls.jsonl").open("a", encoding="ascii") as log:
                print(json.dumps((environ["REQUEST_METHOD"], path, environ["wsgi.url_scheme"],
                                  bool(environ.get("HTTP_X_SIMPLEOFFICE_PEER_SIGNATURE")),
                                  bool(environ.get("HTTP_AUTHORIZATION")))), file=log)
        return app(environ, start_response)

    server = make_server("127.0.0.1", port, observed, threaded=True,
                         ssl_context=(str(tls / (name + ".crt")), str(tls / (name + ".key"))))
    origin = f"https://127.0.0.1:{server.server_port}"
    with app.app_context():
        connection = db.get_db()
        if not connection.execute("SELECT 1 FROM user WHERE username=?", (name,)).fetchone():
            connection.execute("INSERT INTO user(username,password,is_admin) VALUES (?,?,1)",
                               (name, hash_password(os.environ["FIXTURE_PASSWORD"])))
            connection.commit()
            federation_local_identity.save({"peer_id": name, "public_url": origin, "label": name,
                                            "token": os.environ["FIXTURE_BEARER"]})
            SettingsStore(root / "documents").save({"integrations": {
                "federation_public_url": origin, "federation_profile_country": "DE",
            }}, name)
    (root / "port").write_text(str(server.server_port), encoding="ascii")
    server.serve_forever()


class SameOriginRedirect(HTTPRedirectHandler):
    def __init__(self, origin):
        self.origin = urlsplit(origin)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        require((target.scheme, target.netloc) == (self.origin.scheme, self.origin.netloc),
                "The synthetic client rejected a cross-origin redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Browser:
    def __init__(self, origin, context):
        self.origin = origin
        self.client = build_opener(ProxyHandler({}), HTTPSHandler(context=context),
                                   HTTPCookieProcessor(CookieJar()), SameOriginRedirect(origin))

    def request(self, path, *, fields=None, data=None, headers=None, timeout=30):
        target = urlsplit(path)
        require(path.startswith("/") and not path.startswith("//") and not target.netloc and not target.scheme,
                "The synthetic client only uses same-origin paths")
        if fields is not None:
            data = urlencode(fields).encode()
        with self.client.open(Request(self.origin + path, data=data, headers=headers or {}), timeout=timeout) as response:
            return response.read()

    def page(self, path, **kwargs):
        from bs4 import BeautifulSoup

        return BeautifulSoup(self.request(path, **kwargs), "html.parser")

    def csrf(self, path):
        return self.page(path).select_one('meta[name="csrf-token"]')["content"]

    def submit(self, form, values, **kwargs):
        fields = {field["name"]: field.get("value", "") for field in form.select("input[name]")}
        return self.page(form["action"], fields={**fields, **values}, **kwargs)

    def login(self, user, password):
        self.page("/auth/login", fields={"username": user, "password": password,
                                        "_csrf_token": self.csrf("/auth/login")})
        require(bool(self.page(ADMIN + "?view=peers").select_one('form[action="/admin/federation/peers"]')),
                "Synthetic administrator did not reach the Federation page")


def initialize(root):
    root.mkdir()
    for directory in ("home", "documents", "checkout"):
        (root / directory).mkdir()
    checkout = root / "checkout"
    # App startup writes databases and session keys beside its modules. Copy
    # only checked-out source and use read-only source locations for UI assets.
    for name in ("app", "tools"):
        shutil.copytree(ROOT / name, checkout / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("static", "templates"):
        (checkout / name).symlink_to(ROOT / name, target_is_directory=True)
    for source in ROOT.glob("*.py"):
        shutil.copy2(source, checkout / source.name)


@contextmanager
def instance(root, name, tls, context, password, bearer, port=0):
    home = root / "home"
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
           "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"),
           "SIMPLEOFFICE_DOCUMENT_ROOT": str(root / "documents"), "SIMPLEOFFICE_LOG_STDERR_ONLY": "1",
           "SSL_CERT_FILE": str(tls / "ca.crt"), "NO_PROXY": "127.0.0.1", "TMPDIR": str(root),
           "FIXTURE_PASSWORD": password, "FIXTURE_BEARER": bearer}
    ready = root / "port"
    ready.unlink(missing_ok=True)
    log_file = root / "server.log"
    with log_file.open("ab") as log:
        worker = subprocess.Popen([sys.executable, __file__, "--serve", str(root), name, str(port), str(tls)],
                                  cwd=root / "checkout", env=env, stdout=log, stderr=log)
        try:
            until = time.monotonic() + 30
            while time.monotonic() < until and worker.poll() is None:
                if ready.is_file() and (value := ready.read_text(encoding="ascii").strip()).isdigit():
                    bound = int(value)
                    browser = Browser(f"https://127.0.0.1:{bound}", context)
                    try:
                        browser.login(name, password)
                    except (ConnectionError, URLError):
                        time.sleep(0.15)
                        continue
                    yield browser, bound
                    return
                time.sleep(0.1)
            raise RuntimeError(f"Synthetic peer {name} failed to start: {log_file.read_text(errors='replace')[-2400:]}")
        finally:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=5)


def configure_pair(local, remote, local_name, remote_name, token):
    profile = json.loads(remote.request("/.well-known/simpleoffice-federation"))
    profile = profile.get("profile", profile)
    require(profile["peer_id"] == remote_name and profile["base_url"] == remote.origin, "Unexpected peer profile")
    encoded = base64.urlsafe_b64encode(json.dumps({"v": 1, "peer": profile}).encode()).decode().rstrip("=")
    local.page(DISCOVERY + "/qr/import", fields={"payload": "sofp://peer/" + encoded,
                                             "_csrf_token": local.csrf(DISCOVERY)})
    issuer_path = f"{DISCOVERY}/pairing/{local_name}/issue"
    issued = remote.submit(remote.page(DISCOVERY).select_one(f'form[action="{issuer_path}"]'), {})
    code = issued.select_one("p.display-6").get_text(strip=True)
    claim_path = f"{DISCOVERY}/pairing/{remote_name}/claim"
    claim_form = local.page(DISCOVERY).select_one(f'form[action="{claim_path}"]')
    require(claim_form.select_one("[data-pairing-fingerprint]").get_text(strip=True) == profile["fingerprint"]
            and claim_form.select_one("[data-pairing-target]").get_text(strip=True) == remote.origin,
            "Claim form does not show the counterpart's actual fingerprint and TLS address")
    claimed = local.submit(claim_form, {"code": code, "confirm_identity": "1"})
    feedback = " ".join(el.get_text(" ", strip=True) for el in claimed.select(".alert"))
    require("stay unchanged" in feedback or "Trust, Aktivierung und Datenrechte bleiben unverändert" in feedback,
            "Expected actual pairing confirmation before granting rights")
    issuer_row = remote.page(DISCOVERY).select_one(f'form[action="{issuer_path}"]').find_parent("tr")
    require(any(badge.get_text(strip=True) in {"claimed", "beansprucht"}
                for badge in issuer_row.select(".badge")), "The issuer did not retain the claimed-code state")
    peers = local.page(ADMIN + "?" + urlencode({"view": "peers", "edit_peer": remote_name}))
    policy = {"documents": {"send": True, "receive": True, "seed": True},
              "resources": {"documents": {"send": True, "receive": True}}}
    local.submit(peers.select_one(f'form[action="{ADMIN}/peers"]'),
                 {"peer_id": remote_name, "base_url": remote.origin, "label": remote_name,
                  "token": token, "enabled": "1", "policy_json": json.dumps(policy)})


def send_document(browser, remote_name, data):
    listing = browser.page("/documents/")
    boundary = "SyntheticFederation" + secrets.token_hex(12)
    filename = "synthetic-transfer.txt"
    fields = {"_csrf_token": listing.select_one('meta[name="csrf-token"]')["content"], "batch_upload": "1"}
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                    for key, value in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
             "Content-Type: text/plain\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    uploaded = browser.request(listing.select_one("#document-upload-form")["action"], data=body,
                               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    require(json.loads(uploaded) == {"ok": True, "stored": 1}, "Synthetic upload failed")
    files = browser.page(ADMIN + "?" + urlencode({"view": "files", "q": filename}))
    link = next(link["href"] for link in files.select('a[href*="document_id="]') if filename in link.get_text())
    form = browser.page(link).select_one('form[action$="/send"]')
    require(bool(form.select_one(f'#target-peer option[value="{remote_name}"]')), "Destination is not granted")
    browser.submit(form, {"target_peer": remote_name, "operation": "COPY", "start_now": "1"})


def transfer(root, digest, direction):
    database = root / "documents/.simpleoffice-meta/federation.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row, = connection.execute(
            "SELECT transfer_id, status, transferred_bytes, final_path FROM federation_transfer "
            "WHERE blob_hash=? AND direction=?", (digest, direction),
        ).fetchall()
    require(row["status"] == "complete", f"{direction} transfer did not complete")
    return row


def verify_receipt(browser, root, digest, data, transfer_id):
    incoming = transfer(root, digest, "incoming")
    path = Path(incoming["final_path"]).resolve()
    require(incoming["transfer_id"] == transfer_id and incoming["transferred_bytes"] == len(data)
            and path.is_relative_to(root) and path.read_bytes() == data, "Expected the exact verified receipt bytes")
    row = next(row for row in browser.page(ADMIN + "?view=transfers").select("tr") if digest[:12] in row.get_text())
    require("incoming" in row.get_text() and "complete" in row.get_text(), "Expected a visible completed receipt")


def smoke(base):
    tls = base / "certificates"
    tls.mkdir()
    make_certificates(tls)
    context = ssl.create_default_context(cafile=str(tls / "ca.crt"))
    require(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED, "TLS verification must remain active")
    a, b = PEERS
    roots = {name: base / name for name in PEERS}
    passwords = {name: secrets.token_urlsafe(24) for name in PEERS}
    bearer, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for root in roots.values():
        initialize(root)

    def peer(name, port=0):
        return instance(roots[name], name, tls, context, passwords[name], bearer, port)

    sample = "Synthetischer Lieferschein. Die Übertragung bleibt vollständig.\n".encode() * 501
    with peer(a) as (ui_a, port_a), peer(b) as (ui_b, port_b):
        configure_pair(ui_a, ui_b, a, b, token)
        configure_pair(ui_b, ui_a, b, a, token)
        print("PASS: paired and explicitly granted documents in both directions over verified loopback HTTPS", flush=True)
        expected = []
        for local, remote, sender, recipient, data in ((a, b, ui_a, ui_b, b""), (b, a, ui_b, ui_a, sample)):
            send_document(sender, remote, data)
            digest = sha256(data).hexdigest()
            outgoing = transfer(roots[local], digest, "outgoing")
            job = outgoing["transfer_id"]
            verify_receipt(recipient, roots[remote], digest, data, job)
            calls = [json.loads(line) for line in (roots[remote] / "calls.jsonl").read_text().splitlines()]
            prepares = [call for call in calls if call[0] == "POST" and call[1].endswith("/transfers/prepare")]
            chunks = [call for call in calls if call[0] == "PUT" and f"/transfers/{job}/chunks/" in call[1]]
            require(prepares and bool(chunks) == bool(data) and all(
                call[2:] == ["https", True, True] for call in prepares + chunks),
                "Expected signed and bearer-authorized HTTPS, and no chunk PUT for a zero-byte file")
            print(f"PASS: {local} → {remote}, {len(data)} bytes, {len(chunks)} TLS chunk PUT(s)", flush=True)
            expected.append((local, remote, digest, data, job))
    with peer(a, port_a) as (ui_a, _), peer(b, port_b) as (ui_b, _):
        for local, remote, digest, data, job in expected:
            require(transfer(roots[local], digest, "outgoing")["transfer_id"] == job, "Sender job was not retained")
            verify_receipt({a: ui_a, b: ui_b}[remote], roots[remote], digest, data, job)
    print("PASS: both transfers and exact receipt bytes remain after restarting both servers")


def main():
    if len(sys.argv) == 6 and sys.argv[1] == "--serve" and sys.argv[3] in PEERS:
        serve(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]), Path(sys.argv[5]))
        return 0
    if len(sys.argv) != 1:
        return 2
    if sys.platform != "linux" or not shutil.which("openssl"):
        print("SKIP: Linux with the local OpenSSL command is required")
        return 77
    with tempfile.TemporaryDirectory(prefix="simpleoffice-federation-loopback-") as directory:
        smoke(Path(directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
