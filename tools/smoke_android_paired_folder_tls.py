"""Synthetic approved filename search over the existing verified HTTPS LAN proxy."""
from __future__ import annotations

import importlib.util
import json
import os
import secrets
import select
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, HTTPCookieProcessor, ProxyHandler, build_opener
from uuid import uuid4
from wsgiref.simple_server import make_server

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app, db  # noqa: E402
from app.android_paired_folder import bp  # noqa: E402
from app.android_paired_folder_session import folder_profile, phone_call  # noqa: E402
from app.lan_proxy import LanProxyRuntime  # noqa: E402
from app.password_security import hash_password  # noqa: E402


class SyntheticSaf:
    def __init__(self):
        self.searched = threading.Event()

    def readNamesForPc(self, query):
        if not query:
            return '{"status":"ready","names":[]}'
        if query == "bleujay":
            self.searched.set()
            return ('{"status":"ready","names":["Invoice BLEUJAY.pdf"],'
                    '"path":"/PRIVATE/folder","uri":"content://fixture/PRIVATE"}')
        return '{"status":"ready","names":[]}'


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_trust(tls: Path):
    # Use a disposable CA and proper loopback SAN; no verification bypass.
    run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
        "-subj", "/CN=SimpleOffice synthetic test CA", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", str(tls / "ca-key.pem"),
        "-out", str(tls / "ca.pem"), "-days", "1")
    run("openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
        "-subj", "/CN=127.0.0.1", "-keyout", str(tls / "server-key.pem"), "-out", str(tls / "server.csr"))
    ext = tls / "ext.cnf"
    ext.write_text("subjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\n"
                   "extendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature\n"
                   "authorityKeyIdentifier=keyid,issuer\n")
    run("openssl", "x509", "-req", "-in", str(tls / "server.csr"), "-CA", str(tls / "ca.pem"),
        "-CAkey", str(tls / "ca-key.pem"), "-CAcreateserial", "-days", "1", "-extfile", str(ext),
        "-out", str(tls / "server.pem"))
    (tls / "server-key.pem").chmod(0o600)


def login(opener, origin: str, credentials: dict):
    with opener.open(origin + "/auth/login", timeout=8) as page:
        csrf = BeautifulSoup(page.read(), "html.parser").select_one('meta[name="csrf-token"]')["content"]
    with opener.open(origin + "/auth/login", urlencode({**credentials, "_csrf_token": csrf}).encode(),
                     timeout=8) as page:
        assert "/auth/login" not in page.geturl()
        page.read()


def verify(base: Path) -> None:
    credentials = {"username": "synthetic-pc-owner", "password": secrets.token_urlsafe(24)}
    tls, documents = (base / name for name in ("tls", "docs"))
    for directory in (tls, documents):
        directory.mkdir(mode=0o700)
    os.environ["SIMPLEOFFICE_LAN_PROXY_TLS_DIR"] = str(tls)
    build_trust(tls)
    app.config.update(DATABASE=str(base / "auth.sqlite3"), DOCUMENT_ROOT=str(documents),
                      TESTING=False, SECRET_KEY=secrets.token_bytes(32))
    app.register_blueprint(bp)
    with app.app_context():
        db.ensure_auth_database()
        conn = db.get_db()
        row = conn.execute("INSERT INTO user(username,password,is_admin) VALUES(?,?,1)",
                           (credentials["username"], hash_password(credentials["password"])))
        conn.commit()
        profile = folder_profile(app.secret_key, row.lastrowid)
    spec = importlib.util.spec_from_file_location("embedded_android_runtime",
                                                 "android/apk/app/src/main/python/android_runtime.py")
    embedded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(embedded)
    server = make_server("127.0.0.1", 0, app, server_class=embedded.PooledServer,
                         handler_class=embedded.QuietRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    runtime = LanProxyRuntime()
    generation = uuid4().hex
    ui = None
    try:
        status = runtime.start(app, {"enabled": True, "bind_host": "127.0.0.1", "port": port,
                                     "tls_cert": "server.pem", "tls_key": "server-key.pem"},
                               backend_host="127.0.0.1", backend_port=server.server_port, document_root=documents)
        assert status["state"] == "running", status
        origin = f"https://127.0.0.1:{port}"
        verify_tls = ssl.create_default_context(cafile=str(tls / "ca.pem"))
        jar = CookieJar()
        pc = build_opener(ProxyHandler({}), HTTPSHandler(context=verify_tls), HTTPCookieProcessor(jar))
        with pc.open(origin + "/android/pc-folders", timeout=8) as reply:
            assert "/auth/login" in reply.geturl()
            reply.read()
        login(pc, origin, credentials)
        with pc.open(origin + "/documents/local-storage", timeout=8) as reply:
            link = BeautifulSoup(reply.read(), "html.parser").select_one('a[href="/android/pc-folders"]')
            assert link, "Reachable PC link absent from Local folders"
        target = link["href"]
        with pc.open(origin + target, timeout=8) as reply:
            assert reply.headers["Cache-Control"] == "private, no-store"
            assert b"paired-folder-search" in reply.read()
        cookie = next(cookie for cookie in jar if cookie.name == app.config["SESSION_COOKIE_NAME"])
        assert cookie.secure and "HttpOnly" in cookie._rest

        direct = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        direct_origin = f"http://127.0.0.1:{server.server_port}"
        login(direct, direct_origin, credentials)
        try:
            direct.open(direct_origin + target, timeout=8)
            raise AssertionError("PC source page available without existing HTTPS LAN proxy")
        except HTTPError as exc:
            assert exc.code == 404
        native = SyntheticSaf()
        assert json.loads(phone_call("enable", profile, generation))["ok"]
        ui = subprocess.Popen(
            ["node", "tests/android_paired_folder_browser.cjs"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
            env={**os.environ, "NODE_EXTRA_CA_CERTS": str(tls / "ca.pem")},
        )
        ui.stdin.write(json.dumps({"url": origin + target, "cookie": f"{cookie.name}={cookie.value}"}) + "\n")
        ui.stdin.flush()
        for expected in ("requested", "paired", "matched", "unshared", "reshared", "revoked"):
            assert select.select([ui.stdout], [], [], 17)[0], f"no DOM state: {expected}"
            line = ui.stdout.readline()
            assert line, f"DOM exited before {expected}"
            observed = json.loads(line)
            assert observed["event"] == expected, observed
            if expected == "requested":
                code = observed["fingerprint"]
                assert code == json.loads(phone_call("status", profile, generation))["fingerprint"]
                assert json.loads(phone_call("pair", profile, generation, code))["ok"]
            elif expected == "paired":
                assert not native.searched.is_set()
                assert json.loads(phone_call("share", profile, generation, code, native))["ok"]
            elif expected == "matched":
                assert native.searched.is_set()
                assert json.loads(phone_call("unshare", profile, generation, code))["ok"]
            elif expected == "unshared":
                assert json.loads(phone_call("share", profile, generation, code, native))["ok"]
            elif expected == "reshared":
                assert json.loads(phone_call("stop", profile, generation))["ok"]
            else:
                break
            ui.stdin.write("ok\n")
            ui.stdin.flush()
        ui.stdin.close()
        assert ui.wait(timeout=12) == 0
        assert not list(documents.rglob("Invoice*"))
        print("PASS authenticated real loopback TLS and served JS with Node WebCrypto/jsdom: separate pair/share, one matching name only, revoke clears result. No browser engine or physical Android.")
    finally:
        phone_call("stop", profile, generation)
        if ui and ui.poll() is None:
            ui.terminate()
            ui.wait(timeout=10)
        runtime.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="android-paired-folder-ui-") as directory:
        verify(Path(directory))
