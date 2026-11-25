"""Disposable peer preparation, normal web journeys and redacted evidence.

The Docker driver supplies all configuration on stdin; this module never
prints credentials or accepts real user documents. Runtime paths and ids are
intentionally specific to the fixture.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit

from tools.smoke_federation_loopback import ADMIN, Browser, configure_pair, require

PEERS = ("deployment-a", "deployment-b")
STATE = Path("/var/lib/simpleoffice4me")
FIXTURE_ENV = "SIMPLEOFFICE_FEDERATION_DEPLOYMENT_FIXTURE"
TLS = STATE / "fixture-tls"
PLAIN = "http://127.0.0.1:8080"
# Larger than the maximum block, so even a single max-size boundary
# produces multiple network blocks. Avoid excess recurring manifest work.
SIZE = 4 * 1024 * 1024 + 97
QUEUE_HTTP_TIMEOUT = 100
SERVER_SETTINGS = "/admin/settings/server"
TRANSFER_EVENTS = {"scoped_content_blocks_reused", "content_blocks_reused", "download_chunk_completed"}
REQUEST_EVENTS = {"download_requested", "download_deferred", "download_completed"}
STATUS = {"queued", "waiting_peer", "retry", "complete", "running"}
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


@dataclass(frozen=True)
class Site:
    name: str
    ip: str
    password: str

    @property
    def origin(self):
        return f"https://{self.ip}:8081"


def sites(fixture):
    output = {}
    for name in PEERS:
        ip = ipaddress.IPv4Address(fixture["addresses"][name])
        require(str(ip).startswith("10.213."), "Expected a fixed private fixture IP")
        output[name] = Site(name, str(ip), fixture["passwords"][name])
    require(len({site.ip for site in output.values()}) == 2, "Peers need independent IPs")
    return output


def _private_write(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as target:
        target.write(data.encode("ascii"))


def _guard_local_fixture(fixture, name):
    require(name in PEERS and re.fullmatch(r"so-fed-deployment-[0-9a-f]{12}",
            os.environ.get(FIXTURE_ENV, "")) is not None
            and os.environ[FIXTURE_ENV] == fixture.get("run"),
            "This action is limited to the disposable Docker fixtures")
    require(os.environ.get("SIMPLEOFFICE_DOCUMENT_ROOT") == str(STATE / "documents"),
            "Refusing non-fixture document root")


def prepare_peer(fixture, name):
    _guard_local_fixture(fixture, name)
    from app import app
    from app import federation_local_identity
    from app.settings_store import SettingsStore

    site = sites(fixture)[name]
    require(Path(app.config["DOCUMENT_ROOT"]).resolve() == STATE / "documents",
            "Refusing to seed a non-fixture document root")
    require(not (STATE / "config/simpleoffice/app-config.json").exists(), "Fixture volume is not fresh")
    TLS.mkdir(mode=0o700, exist_ok=False)
    for file, mode in (("lan-proxy.crt", 0o644), ("lan-proxy.key", 0o600), ("ca.crt", 0o644)):
        _private_write(TLS / file, fixture["tls"][file], mode)
    with app.app_context():
        # Seed only own endpoint and fixture bearer, never peers, Trust, users
        # or rights. First user and bilateral pairing use normal HTTP forms.
        federation_local_identity.save({
            "peer_id": name, "label": name, "public_url": site.origin,
            "country": "DE", "token": fixture["bearer"],
        })
        SettingsStore(STATE / "documents").save({
            "lan_proxy": {"enabled": True, "bind_host": site.ip, "port": 8081,
                          "tls_cert": "lan-proxy.crt", "tls_key": "lan-proxy.key"},
            "integrations": {"federation_public_url": site.origin, "federation_profile_country": "DE"},
        }, "docker-fixture-setup")
    return {"peer": name, "stage": "self_configured"}


def _context(fixture):
    context = ssl.create_default_context(cadata=fixture["ca"])
    require(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED, "Strict TLS required")
    return context


def reachable(browser, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            browser.request("/auth/login")
            return
        except HTTPError as exc:
            if exc.code not in {502, 503, 504}:
                raise
            time.sleep(0.4)
        except (URLError, ConnectionError, TimeoutError) as exc:
            if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
                raise
            time.sleep(0.4)
    raise RuntimeError("Peer UI did not become ready")


def open_sites(fixture, *, register=False):
    browsers = {}
    for name, site in sites(fixture).items():
        browser = Browser(site.origin, _context(fixture))
        reachable(browser)
        if register:
            browser.page("/auth/register", fields={
                "_csrf_token": browser.csrf("/auth/register"),
                "username": name, "password": site.password,
            })
        browser.login(name, site.password)
        browsers[name] = browser
    return browsers


def identity(browser, name):
    raw = json.loads(browser.request("/.well-known/simpleoffice-federation"))
    profile = raw.get("profile", raw)
    fingerprint = profile["fingerprint"]
    require(profile["peer_id"] == name and profile["base_url"] == browser.origin,
            "Own advertised identity mismatch")
    require(re.fullmatch(r"[0-9a-fA-F:]{32,110}", fingerprint), "Unexpected public fingerprint shape")
    return fingerprint


def pair(fixture):
    browsers = open_sites(fixture, register=True)
    a, b = PEERS
    fingerprints = {name: identity(browser, name) for name, browser in browsers.items()}
    require(fingerprints[a] != fingerprints[b], "Each persistent volume needs its own identity")
    for local, remote in ((a, b), (b, a)):
        configure_pair(browsers[local], browsers[remote], local, remote, fixture["pair_token"])
    return {"identities": fingerprints}


def filename(source, label):
    require(source in PEERS and label in {"initial", "outage"}, "Unexpected fixture specimen")
    return f"{source}-{label}-specimen.bin"


def payload(source, label):
    # Independent non-compressible bytes prevent cross-peer dedup from turning
    # the initial multi-block transfers into local reuse.
    return hashlib.shake_256(filename(source, label).encode("ascii")).digest(SIZE)


def upload(browser, source, label):
    listing = browser.page("/documents/")
    token = listing.select_one('meta[name="csrf-token"]')["content"]
    boundary = "SyntheticDeploy" + secrets.token_hex(12)
    fields = {"_csrf_token": token, "batch_upload": "1"}
    prefix = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    )
    body = prefix + (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename(source, label)}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + payload(source, label) + f"\r\n--{boundary}--\r\n".encode()
    result = browser.request(listing.select_one("#document-upload-form")["action"], data=body,
                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    require(json.loads(result) == {"ok": True, "stored": 1}, "Normal authenticated upload did not store one file")


def only_path(href):
    parts = urlsplit(href)
    require(href.startswith("/") and not href.startswith("//") and not parts.scheme and not parts.netloc,
            "Expected a local link")
    return parts.path + ("?" + parts.query if parts.query else "")


def queue(browser, sender, label):
    peers = browser.page(f"{ADMIN}?view=peers")
    sync = peers.select_one(f'form[action="{ADMIN}/peers/{sender}/sync-index"]')
    require(sync is not None, "No catalog-sync form for granted peer")
    browser.submit(sync, {})
    name = filename(sender, label)
    filters = urlencode({"view": "files", "remote_peer": sender, "remote_q": name})
    files = browser.page(f"{ADMIN}?{filters}")
    forms = [form for form in files.select('#remote-files form[action$="/remote/action"]')
             if form.select_one('[name="action"][value="download"]')]
    require(len(forms) == 1, "Expected a single authorized cached catalog result")
    confirmation = browser.submit(forms[0], {})
    focus = confirmation.select_one("[data-federation-download-focus]")
    require(focus is not None, "Queue confirmation did not offer a focused link")
    href = focus["href"]
    params = parse_qs(urlsplit(href).query)
    job = params.get("download", [""])[0]
    require(bool(UUID.fullmatch(job)) and urlsplit(href).fragment == f"download-{job}"
            and params.get("remote_peer") == [sender] and params.get("remote_q") == [name],
            "Wrong focused job or catalog context")
    duplicate = browser.submit(forms[0], {}).select_one("[data-federation-download-focus]")
    require(duplicate is not None and duplicate["href"] == href, "Pending download was duplicated")
    snapshot = {"job": job, "name": name, "source": sender, "scenario": label, "path": only_path(href)}
    queue_row(browser.page(snapshot["path"]), snapshot, expected="queued")
    return snapshot


def queue_row(page, job, *, expected):
    row = page.select_one(f'#download-{job["job"]}')
    require(row is not None and "table-primary" in row.get("class", []), "Expected the focused row")
    name = job["name"]
    require(name in row.get_text(" ", strip=True), "Unexpected focused filename")
    status_tag = row.select_one("td .badge")
    require(status_tag is not None and status_tag.get_text(strip=True) == expected,
            f"Queue row did not enter {expected}")
    return row


def run_queue(browser, job):
    page = browser.page(job["path"])
    form = page.select_one('form[action$="/queue/run"]')
    require(form is not None, "Queue processing control absent")
    # This regular POST is synchronous. Cold scoped transfers build and
    # verify manifests in both peers; the browser fixture's generic 30s
    # isn't an application limit. Only this operation gets a longer wait.
    return browser.submit(form, {"limit": 1}, timeout=QUEUE_HTTP_TIMEOUT)


def verify_import(browser, job, page=None):
    row = queue_row(page if page is not None else browser.page(job["path"]), job, expected="complete")
    link = row.select_one('a[href^="/documents/"]')
    require(link is not None, "Verified queue job has no local document link")
    href = only_path(link["href"])
    doc_id = href.rsplit("/", 1)[-1]
    require(bool(UUID.fullmatch(doc_id)), "Unexpected imported document identity")
    detail = browser.page(href)
    require(detail.select_one("main h1").get_text(strip=True).endswith(job["name"]), "Wrong imported detail")
    original = next((link for link in detail.select("main a[href]") if
                     link.get_text(" ", strip=True) in {"Open original", "Original öffnen"}), None)
    require(original is not None, "Normal download link absent")
    got = browser.request(only_path(original["href"]))
    expected = payload(job["source"], job["scenario"])
    computed = hashlib.sha256(got).hexdigest()
    require(len(got) == SIZE and computed == hashlib.sha256(expected).hexdigest() and got == expected,
            "Imported bytes/SHA do not match the uploaded specimen")
    return {**job, "document_id": doc_id, "bytes": len(got), "sha256": computed, "status": "complete"}


def progress(step, peer):
    """Emit only fixed test stages and the already-public fixture peer ID."""
    require(peer in PEERS and step in {"uploaded", "queued", "imported"}, "Unexpected progress marker")
    print(f"fixture-progress {step} {peer}", file=sys.stderr, flush=True)


def initial_roundtrip(fixture):
    browsers = open_sites(fixture)
    for name, browser in browsers.items():
        upload(browser, name, "initial")
        progress("uploaded", name)
    a, b = PEERS
    receipts = {}
    for local, remote in ((a, b), (b, a)):
        job = queue(browsers[local], remote, "initial")
        progress("queued", local)
        receipts[local] = verify_import(browsers[local], job, run_queue(browsers[local], job))
        progress("imported", local)
    require(receipts[a]["sha256"] != receipts[b]["sha256"], "Opposite transfers should not reuse identical data")
    return {"receipts": receipts}


def after_restart(fixture):
    browsers = open_sites(fixture)
    for name, browser in browsers.items():
        require(identity(browser, name) == fixture["identities"][name], "Own public identity changed on restart")
        require(verify_import(browser, fixture["receipts"][name]) == fixture["receipts"][name],
                "Completed job or import identity did not survive restart")
    return {"restarted": list(PEERS)}


def prepare_outage(fixture):
    browsers = open_sites(fixture)
    for name, browser in browsers.items():
        upload(browser, name, "outage")
    a, b = PEERS
    return {"outage_jobs": {name: queue(browsers[name], remote, "outage")
                            for name, remote in ((a, b), (b, a))}}


def _local_ui(fixture, name):
    _guard_local_fixture(fixture, name)
    browser = Browser(PLAIN, _context(fixture))  # Only same-container local maintenance.
    browser.login(name, sites(fixture)[name].password)
    return browser


def proxy_action(fixture, name, action):
    browser = _local_ui(fixture, name)
    settings = browser.page(SERVER_SETTINGS)
    form = settings.select_one(f'form:has(button[name="action"][value="{action}"])')
    require(form is not None and form["action"].endswith("/settings/server/lan-proxy"),
            "Expected normal LAN proxy lifecycle form")
    resulting = browser.submit(form, {"action": action})
    state = "running" if action == "start" else "disabled"
    status = next((tag for tag in resulting.select("p.mb-1")
                   if tag.get_text(strip=True).startswith(("Current status:", "Aktueller Status:"))), None)
    require(status is not None and status.select_one("strong").get_text(strip=True) == state,
            f"Local LAN proxy did not report {state}")
    return {"peer": name, "local_https": state}


def outbound_during_outage(fixture):
    name = PEERS[0]
    local = _local_ui(fixture, name)
    job = fixture["outage_jobs"][name]
    return {"outbound_receipt": verify_import(local, job, run_queue(local, job))}


def peer_waiting(fixture):
    a, b = PEERS
    endpoint_a = Browser(sites(fixture)[a].origin, _context(fixture))
    try:
        endpoint_a.request("/auth/login")
    except URLError as exc:
        require(isinstance(exc.reason, ConnectionRefusedError), "Expected a stopped inbound listener, not a TLS failure")
    else:
        raise AssertionError("Stopped peer TLS listener still answered")
    browser = Browser(sites(fixture)[b].origin, _context(fixture))
    browser.login(b, sites(fixture)[b].password)
    job = fixture["outage_jobs"][b]
    page = run_queue(browser, job)
    row = queue_row(page, job, expected="waiting_peer")
    panel = page.select_one("#download-queue-eligibility")
    require((panel["data-ready"], panel["data-delayed"]) == ("0", "1"), "Expected one delayed retry")
    stamp = row.select_one("time")["datetime"]
    require(panel.select_one("time")["datetime"] == stamp, "Inconsistent retry timestamp")
    after = int(datetime.fromisoformat(stamp).timestamp())
    require(after > time.time(), "Retry was not future-dated")
    queue_row(run_queue(browser, job), job, expected="waiting_peer")
    return {"waiting": {"peer": b, "job": job["job"], "status": "waiting_peer", "retry_at": after}}


def peer_recovered(fixture):
    a, b = PEERS
    browsers = open_sites(fixture)
    require(identity(browsers[a], a) == fixture["identities"][a], "Restarted TLS listener changed identity")
    target = fixture["waiting"]["retry_at"]
    delay = max(0.0, target - time.time() + 0.2)
    require(delay <= 18, "Unexpected first retry delay")
    time.sleep(delay)
    job = fixture["outage_jobs"][b]
    row = browsers[b].page(job["path"])
    queue_row(row, job, expected="waiting_peer")
    panel = row.select_one("#download-queue-eligibility")
    require((panel["data-ready"], panel["data-delayed"]) == ("1", "0"), "Retry not ready after real delay")
    return {"recovered_receipt": verify_import(browsers[b], job, run_queue(browsers[b], job))}


def redacted_storage(fixture, name):
    """Collect bounded, redacted fixture queue/event counters using existing storage readers."""
    _guard_local_fixture(fixture, name)
    from app.federation_catalog import FederationCatalog
    from app.federation_store import FederationStore

    tracked = set()
    catalog = FederationCatalog(STATE / "documents")
    federation = FederationStore(STATE / "documents")
    rows = []
    for row in catalog.queue(100):
        if row["path"].rsplit("/", 1)[-1] not in {
            filename(peer, scenario) for peer in PEERS for scenario in ("initial", "outage")
        } or row["status"] not in STATUS:
            continue
        tracked.add(row["request_id"])
        rows.append({"job": row["request_id"], "status": row["status"], "attempts": int(row["attempts"]),
                     "retry_at": int(row["next_attempt_at"])})
    events = []
    for item in catalog.events(200):
        if item.get("request_id") in tracked and item.get("action") in REQUEST_EVENTS:
            events.append({"job": item["request_id"], "kind": item["action"]})
    for item in federation.events(200):
        key = item.get("action")
        job = item.get("transfer_id")
        if job in tracked and key in TRANSFER_EVENTS:
            info = item.get("detail")
            item_data = {"job": job, "kind": key}
            if isinstance(info, dict):
                counts = ("network_blocks", "local_blocks", "network_bytes", "total_bytes", "chunk")
                item_data.update({k: info[k] for k in counts if type(info.get(k)) is int and info[k] >= 0})
            events.append(item_data)
    return {"peer": name, "queue": rows, "events": events}


PHASES = {
    "pair": pair, "initial": initial_roundtrip, "after-restart": after_restart,
    "queue-outage": prepare_outage, "outbound-during-outage": outbound_during_outage,
    "waiting": peer_waiting, "recovered": peer_recovered,
}


def main():
    require(2 <= len(sys.argv) <= 3, "Expected a fixture action")
    action = sys.argv[1]
    fixture = json.load(sys.stdin)
    if action == "prepare" and len(sys.argv) == 3:
        output = prepare_peer(fixture, sys.argv[2])
    elif action in {"stop-proxy", "start-proxy"} and len(sys.argv) == 3:
        output = proxy_action(fixture, sys.argv[2], action.split("-")[0])
    elif action == "status" and len(sys.argv) == 3:
        output = redacted_storage(fixture, sys.argv[2])
    else:
        output = PHASES[action](fixture)
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
