"""Opt-in real TLS acceptance: private book-scanner sharing and Reader.

Use --loopback for three local application processes, or --docker-image after
the two-container Federation Deployment fixture is present in this checkout.
No fallback or fabricated Docker result; only generated synthetic scan pages.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from contextlib import ExitStack
from hashlib import sha256
import io
import json
import shutil
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

from tools import smoke_federation_loopback as fixture
from tools.smoke_federation_loopback import ADMIN, configure_pair, initialize, instance, make_certificates

LOOPBACK_PEERS = (*fixture.PEERS, "loop-office-c")
DOCKER_STAGES = ("private-scratch", "recognition-tools", "private-export", "explicit-publication", "verified-retrieval",
                 "revocation", "explicit-reader-import", "stock-ocr", "private-intake")


def image_file(label, *, sideways=False):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1000, 1300), "white")
    # Pillow supplies the embedded Aileron face as bytes; no system font lookup.
    font = ImageFont.load_default(size=61)
    ImageDraw.Draw(image).text((95, 235), label, fill="black", font=font)
    if sideways:
        image = image.transpose(Image.Transpose.ROTATE_90)
    target = io.BytesIO()
    image.save(target, format="PNG")
    return target.getvalue()



def require_stock_recognition():
    """Only the real acceptance modes require both tools and the English pack."""
    missing = [tool for tool in ("pdftoppm", "tesseract") if not shutil.which(tool)]
    if missing:
        raise OSError("Missing stock OCR acceptance tools: " + ", ".join(missing))
    data = subprocess.run(["tesseract", "--list-langs"], check=True, capture_output=True,
                          text=True, timeout=8)
    if "eng" not in data.stdout.splitlines():
        raise OSError("Stock Tesseract English data (eng) required.")


def exercise(root, source, target, a, b, third=None, *, progress=lambda _stage: None, restart_source=None):
    from pypdf import PdfReader

    progress("private-export")
    library = source.page("/library")
    link = library.select_one('a[href="/library/book-scan"]')
    scan = source.page(link["href"])
    token = scan.select_one('section[data-book-scanner] form input[name="_csrf_token"]')["value"]
    prefix = "Book" + secrets.token_hex(8)
    body = (
        f'--{prefix}\r\nContent-Disposition: form-data; name="_csrf_token"\r\n\r\n{token}\r\n'
        f'--{prefix}\r\nContent-Disposition: form-data; name="order"\r\n\r\n[[1,1],[0,0]]\r\n'
    ).encode()
    images = (image_file("CAPITEL ALPHA"), image_file("CAPITEL OMEGA", sideways=True))
    for i, image in enumerate(images):
        body += (f'--{prefix}\r\nContent-Disposition: form-data; name="pages"; filename="scan-{i}.png"\r\n'
                 'Content-Type: image/png\r\n\r\n').encode() + image + b"\r\n"
    body += f'--{prefix}--\r\n'.encode()
    artifact = root / "assembled.pdf"
    artifact.write_bytes(source.request(link["href"], data=body,
                         headers={"Content-Type": f"multipart/form-data; boundary={prefix}"}))
    data = artifact.read_bytes()
    assert len(PdfReader(io.BytesIO(data)).pages) == 2

    def refreshed():
        peers = target.page(ADMIN + "?view=peers")
        form = peers.select_one(f'form[action="{ADMIN}/peers/{a}/sync-index"]')
        assert form is not None
        target.submit(form, {})
        return target.page(ADMIN + f"?view=files&remote_peer={a}&remote_q=assembled.pdf")

    remote_forms = '#remote-files form[action$="/remote/action"]:has([name="action"][value="download"])'
    assert len(refreshed().select(remote_forms)) == 0, "Unpublished scan should not exist in peer catalog"
    assert "assembled" not in source.page("/documents/reader").get_text(" ", strip=True)

    progress("explicit-publication")
    publication = "/library/book-scan/publications"
    assert source.page(publication).select_one("form[data-book-publish]") is not None
    assert target.page(publication).select_one("form[data-book-receive]") is not None

    def multipart(browser, path, fields, filename, content, field):
        page = browser.page("/documents/" if path == "/documents/upload" else path)
        token = page.select_one('meta[name="csrf-token"]')["content"]
        prefix = "Milestone" + secrets.token_hex(8)
        body = b"".join(
            f'--{prefix}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in {"_csrf_token": token, **fields}.items()
        )
        body += (
            f'--{prefix}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            'Content-Type: application/pdf\r\n\r\n'
        ).encode() + content + f'\r\n--{prefix}--\r\n'.encode()
        return browser.page(path, data=body, headers={"Content-Type": f"multipart/form-data; boundary={prefix}"})

    def expect_error(code, callback):
        try:
            callback()
        except HTTPError as exc:
            exc.close()
            assert exc.code == code, (code, exc.code)
        else:
            raise AssertionError(f"Expected HTTP {code}")

    def publish(confirmed, content=data):
        return multipart(source, publication, {"action": "publish", "peer_id": b, "confirm": confirmed},
                         "assembled.pdf", content, "pdf")

    expect_error(400, lambda: publish(""))
    assert source.page(publication).select_one("form:has([name=action][value=revoke])") is None
    expect_error(409, lambda: publish("1", data + b"\n"))
    offered = publish("1")
    code = offered.select_one("[data-book-offer]")["data-book-offer"]
    assert offered.select_one('[data-book-serve-status="waiting"]') is not None
    assert publish("1").select_one("[data-book-offer]")["data-book-offer"] == code
    assert len(refreshed().select(remote_forms)) == 0, "Explicit scoped publication leaked into ordinary catalog"

    def claim():
        return target.request(publication, fields={"_csrf_token": target.csrf(publication),
                              "action": "receive", "peer_id": a, "offer": code})
    if third is not None:
        progress("third-peer-privacy")
        middle = third.page(ADMIN + "?view=peers")
        third.submit(middle.select_one(f'form[action="{ADMIN}/peers/{a}/sync-index"]'), {})
        catalog = third.page(ADMIN + f"?view=files&remote_peer={a}&remote_q=assembled.pdf")
        assert not catalog.select(remote_forms) and code not in catalog.get_text()
        found = json.loads(third.request("/documents/quick-search/api?q=assembled&type=document"))
        assert found["matches"] == [], "Possession search disclosed the private scan"
        assert not third.page(publication).select_one("form:has([name=action][value=revoke])")
        # Ordinary recipient form, using a fixture-supplied ref: a configured
        # allowed relationship is not a grant for someone else's snapshot.
        expect_error(404, lambda: third.request(publication, fields={
            "_csrf_token": third.csrf(publication), "action": "receive", "peer_id": a, "offer": code,
        }))
        assert source.page(publication).select_one('[data-book-serve-status="waiting"]') is not None
    progress("verified-retrieval")
    received = claim()
    assert received == data
    served = source.page(publication).select_one('[data-book-serve-status="served"] time')
    assert served is not None and served["datetime"].endswith("+00:00")
    assert claim() == data
    assert source.page(publication).select_one('[data-book-serve-status="served"] time')["datetime"] == served["datetime"]
    if restart_source is not None:
        progress("sender-status-after-restart")
        source = restart_source()
        assert source.page(publication).select_one('[data-book-serve-status="served"] time')["datetime"] == served["datetime"]
    assert "assembled" not in target.page("/documents/reader").get_text(" ", strip=True)
    progress("revocation")
    source.submit(source.page(publication).select_one("form:has([name=action][value=revoke])"), {})
    expect_error(404, claim)
    assert source.page(publication).select_one("[data-book-serve-status]") is None
    assert artifact.read_bytes() == data
    progress("explicit-reader-import")
    uploaded = multipart(target, "/documents/upload",
                         {"batch_upload": "1"}, "assembled.pdf", received, "files")
    assert json.loads(uploaded.get_text()) == {"ok": True, "stored": 1}
    reader = target.page("/documents/reader")
    matches = [link for link in reader.select('a[href^="/documents/reader/"]')
               if len(link["href"].rsplit("/", 1)[-1]) == 36]
    assert matches
    opened = target.page(matches[0]["href"])
    assert opened.select_one("h1").get_text(strip=True) == "Book scan"
    doc_id = matches[0]["href"].rsplit("/", 1)[-1]
    detail = target.page("/documents/" + doc_id)
    original = next(link for link in detail.select("main a[href]") if
                    link.get_text(" ", strip=True) in {"Open original", "Original öffnen"})
    assert target.request(original["href"]) == data
    progress("stock-ocr")
    subprocess.run(["pdftoppm", "-r", "110", "-png", str(artifact), str(root / "rendered")],
                   check=True, capture_output=True, timeout=20)
    for page_number, text in ((1, "OMEGA"), (2, "ALPHA")):
        proc = subprocess.run(["tesseract", str(root / f"rendered-{page_number}.png"), "stdout",
                               "-l", "eng", "--psm", "6"], check=True, capture_output=True, timeout=15)
        assert text in proc.stdout.decode(), proc.stdout.decode()
    result = {"bytes": len(data), "sha256": sha256(data).hexdigest(), "pages": 2}
    print(f"VERIFIED: authenticated verified-TLS route, rotated/reordered, one explicit export POST; "
          f"unchanged PDF + explicit recipient-only publication, no generic catalog entry, exact signed retrieval, revoke refused next retrieval, explicit target import and Reader, "
          f"bytes={len(data)} SHA256={result['sha256']}, visual/raster OCR OMEGA then ALPHA.")
    return result



def exercise_mixed_intake(browser):
    """Exercise the rendered local capture and explicit save with no external provider."""
    inbox = browser.page("/inventory/capture")
    path = inbox.select_one('a[href="/inventory/capture/intake"]')["href"]
    image = image_file("Marke: Synthetic Pencil")
    csrf = browser.csrf(path)

    def capture(code, *, photo=True):
        page = browser.page(path)
        token = page.select_one('form#intake-capture input[name="capture_token"]')["value"]
        boundary = "Intake" + secrets.token_hex(8)
        fields = {"_csrf_token": csrf, "capture_token": token, "barcode": code}
        data = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        )
        if photo:
            data += (
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="synthetic.png"\r\n'
                'Content-Type: image/png\r\n\r\n'
            ).encode() + image + b"\r\n"
        data += f'--{boundary}--\r\n'.encode()
        return json.loads(browser.request(path, data=data, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json",
        }))["draft"]

    def action(draft, scope, **fields):
        return json.loads(browser.request(f"{path}/{draft['id']}/{scope}",
            fields={"_csrf_token": csrf, "revision": draft["revision"], **fields},
            headers={"Accept": "application/json"}))["draft"]

    first, second = capture("9780140328721", photo=False), capture("")
    assert not first["ocr"] and not first["catalog"] and not first["confirmed"]
    assert [row["id"] for row in browser.page(path).select("article")] == [
        f"draft-{draft['id']}" for draft in (first, second)
    ]
    assert first["preview_url"] is None and first["manual"]["barcode"] == "9780140328721"
    assert "Ohne Foto" in browser.page(path).select_one(f"#draft-{first['id']}").get_text(" ", strip=True)
    assert browser.request(second["preview_url"]) == image
    recognized = action(second, "ocr")
    row = next(item for item in recognized["evidence"] if item["field"] == "manufacturer")
    assert row["label"] == "OCR" and row["value"] == "Synthetic Pencil"
    selected = action(recognized, "choose", source_id=row["source_id"])
    saved = action(selected, "confirm", title="Synthetic Pencil Fixture", kind="object")
    assert saved["state"] == "confirmed"
    assert saved["confirmed"]["fields"]["manufacturer"] == "Synthetic Pencil"
    assert action(selected, "confirm", title="Unexpected duplicate", kind="object")["confirmed"] == saved["confirmed"]
    assert "In Prüfung" in browser.page(path).select_one(f"#draft-{first['id']}").get_text(" ", strip=True)
    print("Verified mixed intake: ordered private captures, OCR evidence, explicit local save and idempotent replay.")


def loopback(root, *, progress):
    progress("provision")
    fixture.PEERS = LOOPBACK_PEERS
    fixture.__file__ = str(Path(__file__).resolve())
    tls = root / "certificates"
    tls.mkdir()
    make_certificates(tls)
    context = ssl.create_default_context(cafile=str(tls / "ca.crt"))
    passwords = {name: secrets.token_urlsafe(24) for name in LOOPBACK_PEERS}
    shared = secrets.token_urlsafe(32)
    for peer in LOOPBACK_PEERS:
        initialize(root / peer)
    a, b, c = LOOPBACK_PEERS
    with ExitStack() as stack, ExitStack() as source_stack:
        browser, source_port = source_stack.enter_context(instance(root / a, a, tls, context, passwords[a], shared))
        peers = {a: browser, **{
            name: stack.enter_context(instance(root / name, name, tls, context, passwords[name], shared))[0]
            for name in (b, c)
        }}

        def restart_source():
            source_stack.close()
            peers[a], rebound = source_stack.enter_context(instance(
                root / a, a, tls, context, passwords[a], shared, port=source_port,
            ))
            assert rebound == source_port
            return peers[a]

        progress("pairing")
        for neighbor in (b, c):
            token = secrets.token_urlsafe(32)
            configure_pair(peers[a], peers[neighbor], a, neighbor, token)
            configure_pair(peers[neighbor], peers[a], neighbor, a, token)
        result = exercise(root, peers[a], peers[b], a, b, peers[c], progress=progress, restart_source=restart_source)
        progress("private-intake")
        exercise_mixed_intake(peers[a])
        progress("signed-retrieval-audit")
        calls = [json.loads(line) for line in (root / a / "calls.jsonl").read_text().splitlines()]
        signed = [item for item in calls if "/federation/v1/book-scans/" in item[1]]
        assert len(signed) == 4 and all(item[0] == "GET" and item[2] == "https" and item[3] for item in signed)
        print("Verified third configured peer: zero catalog or document search hits, no owned offer and no PDF.")
        return {**result, "paired_peers": 3, "signed_fetch_requests": len(signed), "unselected_peer_denied": True,
                "mixed_intake_confirmed": True, "sender_status_after_restart": True}


def _docker_progress(stage):
    if stage in DOCKER_STAGES:
        print(f"scanner-stage:{stage}", file=sys.stderr, flush=True)


def _docker_completed_stages(stderr):
    prefix = "scanner-stage:"
    return tuple(stage for line in stderr.splitlines() if line.startswith(prefix)
                 and (stage := line[len(prefix):]) in DOCKER_STAGES)


def docker_inside():
    from tools.federation_docker_peer import CLIENT_SCRATCH_MIB, PEERS, open_sites, verify_private_scratch
    verify_private_scratch(CLIENT_SCRATCH_MIB)
    _docker_progress("private-scratch")
    _docker_progress("recognition-tools")
    require_stock_recognition()
    a, b = PEERS
    peers = open_sites(json.load(sys.stdin))
    with TemporaryDirectory(prefix="book-scan-container-") as folder:
        exercise(Path(folder), peers[a], peers[b], a, b, progress=_docker_progress)
        _docker_progress("private-intake")
        exercise_mixed_intake(peers[a])


def docker_host(root, args):
    if not shutil.which("docker"):
        print("Real Docker Engine required; no simulated result.", file=sys.stderr)
        return 1
    try:
        from tools.federation_docker_peer import CLIENT_SCRATCH_MIB
        from tools.smoke_federation_docker import APP_USER, PYTHON, Deployment, _docker_env, _failure, _scratch_args
    except ImportError:
        print("The repository's two-container Federation acceptance fixture is required.", file=sys.stderr)
        return 1
    evidence = args.artifacts.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    tls = root / "certs"
    tls.mkdir()
    deployment = Deployment(args.docker_image, args.html_deps.resolve(), evidence, tls)
    try:
        deployment.prepare()
        deployment.fixture.update(deployment.invoke("pair"))
        name = deployment.prefix + "-scanner"
        deployment.transient.add(name)
        outcome = deployment.command("run", "--rm", "-i", "--name", name, "--pull=never",
            "--network", deployment.network, "--read-only", "--user", APP_USER, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", *_scratch_args(CLIENT_SCRATCH_MIB),
            *deployment._deps(), *_docker_env({"PYTHONPATH": "/run/test-python", **deployment._private_env()}),
            "--entrypoint", PYTHON, deployment.image, "-m", "tools.smoke_book_scans", "--inside",
            fixture=deployment.fixture, timeout=230, check=False)
        # Project only the synthetic success marker; do not copy raw request logs.
        lines = [line for line in outcome.stdout.splitlines() if line.startswith("VERIFIED: ")]
        stages = _docker_completed_stages(outcome.stderr)
        for stage in stages:
            deployment.note("book-scan-stage", "reached", component=stage)
        if outcome.returncode or len(lines) != 1 or stages != DOCKER_STAGES:
            deployment.note("book-scan", "failed", **_failure(outcome))
            return 1
        print(lines[0])
        (evidence / "book-scan-result.txt").write_text(lines[0] + "\n", encoding="utf-8")
        deployment.note("book-scan", "passed")
        return 0
    finally:
        try:
            deployment.collect()
        finally:
            deployment.cleanup()


def loopback_with_evidence(root, evidence):
    stage = "starting"

    def save(status, **fields):
        if evidence is not None:
            evidence.write_text(json.dumps({
                "stage": stage, "status": status, "utc": datetime.now(UTC).isoformat(), **fields,
            }, sort_keys=True) + "\n", encoding="utf-8")

    def progress(next_stage):
        nonlocal stage
        stage = next_stage
        save("in_progress")

    try:
        progress("recognition-tools")
        require_stock_recognition()
        result = loopback(root, progress=progress)
    except (AssertionError, OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        save("failed", error_class=type(exc).__name__)
        print(f"Book scanner acceptance failed at {stage} ({type(exc).__name__}).", file=sys.stderr)
        return 1
    stage = "complete"
    save("passed", **result)
    return 0


def main():
    if not __debug__:
        print("The acceptance checks must run without Python optimization.", file=sys.stderr)
        return 1
    if len(sys.argv) == 6 and sys.argv[1] == "--serve" and sys.argv[3] in LOOPBACK_PEERS:
        fixture.serve(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]), Path(sys.argv[5]))
        return 0
    if sys.argv[1:] == ["--inside"]:
        docker_inside()
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--loopback", action="store_true")
    mode.add_argument("--docker-image")
    parser.add_argument("--html-deps", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--evidence", type=Path, help="Redacted loopback result JSON")
    args = parser.parse_args()
    if args.evidence is not None and not args.loopback:
        parser.error("--evidence is for --loopback only")
    if args.docker_image and (args.html_deps is None or args.artifacts is None):
        parser.error("--docker-image needs --html-deps and --artifacts")
    if not shutil.which("openssl"):
        parser.exit(1, "The local OpenSSL command is required.\n")
    with TemporaryDirectory(prefix="simpleoffice-book-smoke-") as folder:
        if args.loopback:
            return loopback_with_evidence(Path(folder), args.evidence)
        return docker_host(Path(folder), args)


if __name__ == "__main__":
    raise SystemExit(main())
