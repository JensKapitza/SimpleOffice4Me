"""Exercise one workflow copy notice over two ordinary isolated HTTPS instances."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import time
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
                                   "v3.activity": "1", "v3.automation": "1", "v3.federation": "1", "v3.jobs": "1"})
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


def _prepare_copy(root, tls, speaker):
    code = """
import os
from pathlib import Path
from app import app
from app.replication_store import ReplicationStore
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
""" % speaker
    result = _cli(root, tls, ["-c", code])
    assert result.returncode == 0, result.stderr


def _copy(root, tls, speaker, run_id):
    (root / "documents" / ".simpleoffice-meta" / "notes.json").write_text(run_id, encoding="utf8")
    args = ["-m", "flask", "--app", "app", "run-replications", "--actor", speaker,
            "--notify-account", speaker, "--run-id", run_id]
    result = _cli(root, tls, args)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout.splitlines()[-1])
    assert sum(item["copied"] for item in outcome["completed"]) == 1 and not outcome["errors"]
    listed = _cli(root, tls, ["-m", "flask", "--app", "app", "list-workflow-copy-events", "--actor", speaker])
    listed.check_returncode()
    return listed.stdout.splitlines()[0].split()[0]


def _work(root, tls):
    result = _cli(root, tls, ["-m", "tools.v3_job_worker", "--root", str(root / "documents"), "--once"])
    assert result.returncode == 0, result.stderr


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
        destination_server = stack.enter_context(ExitStack())
        destination_password = secrets.token_urlsafe(24)
        dst, destination_port = destination_server.enter_context(
            fixture.instance(root / b, b, tls, context, destination_password, bearer),
        )
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
        create = "/automation-v3/peer-copy-rule"
        form = src.page("/automation-v3").select_one(f'form[action="{create}"]')
        assert form
        src.submit(form, {"peer": b, "destination": grant, "source": "job", "delay": "0",
                          "deadline": "120", "zone": "Europe/Berlin"})
        _prepare_copy(root / a, tls, a)
        event_id = _copy(root / a, tls, a, "synthetic-workflow-run")
        assert not json.loads(dst.request("/automation-v3/copy-notices"))["notices"]
        assert src.page("/automation-v3").select_one('[data-delivery-state="peer_queued"]')
        _work(root / a, tls)
        assert len(src.page("/automation-v3").select('[data-delivery-state="delivered"]')) == 1
        args = ["-m", "flask", "--app", "app", "send-workflow-copy-notice",
                "--peer", b, "--destination", grant, "--actor", a, "--event-id", event_id]
        result = _cli(root / a, tls, args)
        result.check_returncode()
        assert result.stdout.splitlines()[-1] == "duplicate"
        target = json.loads(dst.request("/automation-v3/copy-notices"))["notices"]
        assert len(target) == 1 and target[0]["kind"] == "peer"
        assert set(target[0]) == {"id", "kind", "at"}
        assert not json.loads(src.request("/automation-v3/copy-notices"))["notices"]
        destination_server.close()
        _copy(root / a, tls, a, "synthetic-workflow-run-reconnect")
        _work(root / a, tls)
        assert src.page("/automation-v3").select_one('[data-delivery-state="peer_unreachable"]')
        dst, _ = destination_server.enter_context(
            fixture.instance(root / b, b, tls, context, destination_password, bearer, port=destination_port),
        )
        time.sleep(5.1)  # Initial durable retry backoff, with the real worker restarted below.
        _work(root / a, tls)
        target = json.loads(dst.request("/automation-v3/copy-notices"))["notices"]
        assert len(target) == 2 and len({row["id"] for row in target}) == 2
        source_page = src.page("/automation-v3")
        assert not source_page.select('[data-delivery-state="peer_unreachable"]')
        rows = source_page.select('[data-delivery-state="delivered"]')
        assert len(rows) == 2 and any(": 2" in row.get_text() for row in rows)
        _copy(root / a, tls, a, "synthetic-workflow-run-revoked")
        dst.page(consent, fields={"operation": "revoke", "grant": grant, "_csrf_token": dst.csrf("/automation-v3")})
        _work(root / a, tls)
        assert src.page("/automation-v3").select_one('[data-delivery-state="denied"]')
        assert json.loads(dst.request("/automation-v3/copy-notices"))["notices"] == target
        print("PASS: real copy triggers durable peer job; paired HTTPS delivery, replay, offline recovery, and revoke verified")



if __name__ == "__main__":
    main()
