"""Exercise one workflow copy notice over two ordinary isolated HTTPS instances."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

from tools import smoke_federation_loopback as fixture


def _cli(root, tls, arguments):
    checkout = root / "checkout"
    env = {"PATH": os.environ.get("PATH", os.defpath), "PYTHONPATH": str(checkout),
           "HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "home" / "config"),
           "XDG_DATA_HOME": str(root / "home" / "data"),
           "SIMPLEOFFICE_DOCUMENT_ROOT": str(root / "documents"),
           "SIMPLEOFFICE_LOG_STDERR_ONLY": "1", "SSL_CERT_FILE": str(tls / "ca.crt")}
    return subprocess.run([sys.executable, *arguments], cwd=checkout, env=env,
                          text=True, capture_output=True, timeout=20)


def _enable(browser, counterpart, *, direction):
    endpoint = "/admin/v3/capabilities"
    browser.page(endpoint, fields={"_csrf_token": browser.csrf(endpoint),
                                   "v3.activity": "1", "v3.automation": "1", "v3.federation": "1"})
    trust = fixture.DISCOVERY + "/trust/" + counterpart
    browser.page(trust, fields={"_csrf_token": browser.csrf(fixture.DISCOVERY),
                               "trust_level": "NORMAL", "verification": "VERIFIED_ADMIN"})
    return {"data_classes": {"workflow_notices": {direction: True}}}


def _set_policy(browser, peer, remote, policy):
    admin = fixture.ADMIN
    browser.page(admin + "/peers", fields={
        "_csrf_token": browser.csrf(admin + "?view=peers"), "peer_id": peer, "label": peer,
        "base_url": remote, "enabled": "1", "policy_json": json.dumps(policy),
    })


def _produce_copy(root, tls, speaker):
    code = """
import os
from pathlib import Path
from app import app
from app.replication_store import ReplicationStore
from app.v3_activity import ActivityStore
from app.document_copy_events import JOB_EVENT_NAME
document = Path(os.environ["SIMPLEOFFICE_DOCUMENT_ROOT"])
actor = %r
with app.app_context():
    store = ReplicationStore(document)
    store.control.mkdir(exist_ok=True)
    (store.control / "notes.json").write_text("synthetic copy data", encoding="utf8")
    destination = document.parent / "backup"
    destination.mkdir()
    target = store.add_target({"label": "Backup", "path": str(destination)}, actor)
    store.add_rule({"label": "Notes", "target_id": target["target_id"], "categories": ["notes"]}, actor)
    outcome = store.run_all(actor, run_id="synthetic-workflow-run")
    assert sum(item["copied"] for item in outcome["completed"]) == 1 and not outcome["errors"]
    event, = ActivityStore(document).list(actor=actor, event_type=JOB_EVENT_NAME)
    print("COPY-EVENT", event.event_id)
""" % speaker
    result = _cli(root, tls, ["-c", code])
    result.check_returncode()
    return next(line.split()[1] for line in result.stdout.splitlines() if line.startswith("COPY-EVENT "))


def main():
    with TemporaryDirectory(prefix="simpleoffice-workflow-peers-") as folder, ExitStack() as stack:
        root = Path(folder)
        tls = root / "tls"
        tls.mkdir()
        fixture.make_certificates(tls)
        context = ssl.create_default_context(cafile=str(tls / "ca.crt"))
        a, b = fixture.PEERS
        for name in (a, b):
            fixture.initialize(root / name)
        bearer = secrets.token_urlsafe(32)
        src, _ = stack.enter_context(fixture.instance(root / a, a, tls, context, secrets.token_urlsafe(24), bearer))
        dst, _ = stack.enter_context(fixture.instance(root / b, b, tls, context, secrets.token_urlsafe(24), bearer))
        pairing = secrets.token_urlsafe(32)
        fixture.configure_pair(src, dst, a, b, pairing)
        fixture.configure_pair(dst, src, b, a, pairing)
        _set_policy(src, b, dst.origin, _enable(src, b, direction="send"))
        _set_policy(dst, a, src.origin, _enable(dst, a, direction="receive"))

        consent = "/automation-v3/peer-notices"
        try:
            dst.page(consent, fields={"operation": "grant", "peer": a})
        except HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("CSRF was not enforced")
        page = dst.page(consent, fields={"operation": "grant", "peer": a,
                                        "_csrf_token": dst.csrf("/automation-v3")})
        grant = page.select_one('input[name=grant]')["value"]
        event_id = _produce_copy(root / a, tls, a)
        args = ["-m", "flask", "--app", "app", "send-workflow-copy-notice",
                "--peer", b, "--destination", grant, "--actor", a, "--event-id", event_id]
        for expected in ("delivered", "duplicate"):
            result = _cli(root / a, tls, args)
            result.check_returncode()
            assert result.stdout.splitlines()[-1] == expected
        target = json.loads(dst.request("/automation-v3/copy-notices"))["notices"]
        assert len(target) == 1 and target[0]["kind"] == "peer"
        assert set(target[0]) == {"id", "kind", "at"}
        assert not json.loads(src.request("/automation-v3/copy-notices"))["notices"]
        dst.page(consent, fields={"operation": "revoke", "grant": grant, "_csrf_token": dst.csrf("/automation-v3")})
        denied = _cli(root / a, tls, args)
        assert denied.returncode != 0 and denied.stdout.splitlines()[-1] == "denied"
        assert json.loads(dst.request("/automation-v3/copy-notices"))["notices"] == target
        print("PASS: paired HTTPS instances, real copied file, one private remote notice, duplicate and revoke verified")


if __name__ == "__main__":
    main()
