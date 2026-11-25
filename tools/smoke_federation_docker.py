"""Run the normal SimpleOffice image twice and verify federation deployment.

Requires a real Docker Engine. No alternate in-process or simulated runtime,
nor Docker socket inside any fixture. Only disposable resources created here
are removed. See deploy/docker/README.md for portable commands.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from tools.federation_docker_peer import (
    APP_GID, APP_UID, CLIENT_SCRATCH_MIB, FIXTURE_ENV, PEERS, PEER_SCRATCH_MIB, SCRATCH, SIZE, STATE,
)
from tools.smoke_federation_loopback import require

APP_USER = f"{APP_UID}:{APP_GID}"
PYTHON = "/opt/simpleoffice4me/.venv/bin/python"
DUMMY_ARTIFACT_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z")
LOG_KINDS = {
    "SimpleOffice4Me läuft mit Waitress": "web-ready",
    "federation catalog sync started": "catalog-start",
    "federation catalog sync completed": "catalog-complete",
    "federation download deferred": "download-deferred",
    "federation block dedupe": "download-blocks",
    "LAN proxy startup failed": "lan-proxy-start-failed",
    "LAN-Proxy konnte nicht starten": "lan-proxy-start-failed",
    "LAN proxy upstream failed": "lan-proxy-upstream-failed",
    "TLS": "tls-notice",
    "Traceback": "application-exception",
    "ERROR": "application-error",
}
START_STAGES = {"status", "prepare", "pair", "initial", "after-restart", "queue-outage",
                "stop-proxy", "start-proxy", "outbound-during-outage", "waiting", "recovered"}


def _openssl(directory, *args):
    subprocess.run(["openssl", *args], cwd=directory, check=True, capture_output=True, timeout=20)


def create_tls(directory, addresses):
    _openssl(directory, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key",
             "-out", "ca.crt", "-days", "2", "-subj", "/CN=Disposable SimpleOffice Deployment CA",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-addext", "subjectKeyIdentifier=hash")
    for serial, (name, ip) in enumerate(addresses.items(), 30):
        _openssl(directory, "req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key",
                 "-out", f"{name}.req", "-subj", f"/CN={name}")
        ext = directory / f"{name}.ext"
        ext.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n"
                       f"extendedKeyUsage=serverAuth\nsubjectAltName=IP:{ip}\n", encoding="ascii")
        _openssl(directory, "x509", "-req", "-in", f"{name}.req", "-CA", "ca.crt", "-CAkey", "ca.key",
                 "-set_serial", str(serial), "-out", f"{name}.crt", "-days", "2", "-extfile", ext.name)


def _utc():
    return datetime.now(UTC).isoformat()


def _events(log):
    """Project Docker stderr/stdout onto diagnostic events, not raw secrets."""
    for line in log.splitlines():
        matched = next((kind for source, kind in LOG_KINDS.items() if source in line), None)
        if not matched:
            continue
        emitted = {"kind": matched}
        if stamp := DUMMY_ARTIFACT_STAMP.match(line):
            emitted["utc"] = stamp[0]
        if key := re.search(r"\brequest=([0-9a-f-]{36})\b", line):
            emitted["job"] = key[1]
        for field in ("attempts", "retry_in", "network_blocks", "network_bytes"):
            if number := re.search(rf"\b{field}=(\d{{1,12}})\b", line):
                emitted[field] = int(number[1])
        yield emitted


def _failure(result):
    """Never return raw stdout/stderr: requests can contain credentials."""
    text = result.stderr
    error = re.search(r"^([A-Za-z.]+Error|[A-Za-z.]+Expired):", text, re.MULTILINE)
    status = re.search(r"HTTP Error (\d{3})", text)
    # Our require() diagnostics are literal code-owned text; discard everything
    # after the first line and any value containing URL/header syntax.
    progress = re.findall(r"^fixture-progress (uploaded|queued|imported) (deployment-[ab])$", text, re.MULTILINE)
    assertion = re.search(r"^AssertionError: ([A-Za-z0-9 .,-]{1,176})$", text, re.MULTILINE)
    out = {"exit": result.returncode, "class": error[1] if error else "command"}
    if progress:
        out["last_progress"], out["peer"] = progress[-1]
    if status:
        out["http_status"] = int(status[1])
    if assertion:
        out["assertion"] = assertion[1]
    return out


def _scratch_args(size_mib):
    mount = f"{SCRATCH}:rw,nosuid,nodev,noexec,size={size_mib}m,mode=0700,uid={APP_UID},gid={APP_GID}"
    return ("--tmpfs", mount, "-e", f"TMPDIR={SCRATCH}")


class Deployment:
    def __init__(self, image, deps, output, tls):
        self.image, self.deps, self.output, self.tls = image, deps, output, tls
        self.prefix = "so-fed-deployment-" + secrets.token_hex(6)
        self.network = self.prefix
        self.volumes = set()
        self.containers = set()
        self.transient = set()
        self.has_network = False
        self.fixture = {
            "run": self.prefix,
            "passwords": {name: secrets.token_urlsafe(22) for name in PEERS},
            "bearer": secrets.token_urlsafe(32), "pair_token": secrets.token_urlsafe(32),
        }

    def command(self, *args, fixture=None, timeout=120, check=True):
        proposed = subprocess.run(
            ["docker", *args], input=json.dumps(fixture) if fixture is not None else None,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        if check and proposed.returncode:
            raise RuntimeError(json.dumps(_failure(proposed)))
        return proposed

    def note(self, phase, status, **fields):
        event = {"utc": _utc(), "stage": phase, "status": status, **fields}
        with (self.output / "timeline.jsonl").open("a", encoding="utf-8") as stream:
            print(json.dumps(event, sort_keys=True), file=stream)
        print(f"{phase}: {status}", flush=True)

    def name(self, peer):
        require(peer in PEERS, "Unknown fixture peer")
        return f"{self.prefix}-{peer}"

    def provision_network(self):
        # A private user-defined bridge with no external connectivity enables
        # literal fixture IPs and strict IP SAN certificate validation.
        for _ in range(8):
            subnet = ipaddress.ip_network(f"10.213.{secrets.randbelow(256)}.0/28")
            result = self.command("network", "create", "--internal", "--subnet", str(subnet),
                                  self.network, check=False)
            if result.returncode == 0:
                self.has_network = True
                self.fixture["addresses"] = {name: str(subnet[index]) for index, name in enumerate(PEERS, 2)}
                return
            if "overlap" not in result.stderr.lower():
                break
        raise RuntimeError("Could not provision an isolated internal Docker bridge")

    def _owner_env(self):
        plain = {
            FIXTURE_ENV: self.prefix,
            "HOME": str(STATE), "XDG_CONFIG_HOME": str(STATE / "config"),
            "SIMPLEOFFICE_HOST": "127.0.0.1", "SIMPLEOFFICE_PORT": "8080",
            "SIMPLEOFFICE_CONTAINER_ROLE": "web",
            # docker exec does not inherit exports done by the entrypoint.
            "SIMPLEOFFICE_LOG_STDERR_ONLY": "1",
            "SIMPLEOFFICE_BACKGROUND_INDEX": "0", "SIMPLEOFFICE_OSM_INDEX": "0",
            "SIMPLEOFFICE_DATALOGGER": "0", "SIMPLEOFFICE_MCP": "0",
            "SIMPLEOFFICE_GITHUB_ERROR_REPORTING": "0", "SIMPLEOFFICE_ERROR_REPORT_URL": "",
            "SIMPLEOFFICE_LAN_PROXY_TLS_DIR": str(STATE / "fixture-tls"),
            "SSL_CERT_FILE": str(STATE / "fixture-tls/ca.crt"),
        }
        return _docker_env({**plain, **self._private_env()})

    def _private_env(self):
        scope = ",".join(("127.0.0.1", *self.fixture["addresses"].values()))
        return {**dict.fromkeys(("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                                "ALL_PROXY", "all_proxy"), ""), "NO_PROXY": scope, "no_proxy": scope}

    def _deps(self):
        return ("--mount", f"type=bind,src={self.deps},dst=/run/test-python,readonly")

    def _app_args(self, peer):
        return ("--user", APP_USER, "--init", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", *_scratch_args(PEER_SCRATCH_MIB),
                "--mount", f"type=volume,src={self.name(peer)},dst={STATE}",
                *self._deps(), *self._owner_env())

    def prepare(self):
        docker = self.command("info", "--format", "{{.ServerVersion}}", timeout=20).stdout.strip()
        image = self.command("image", "inspect", "--format", "{{.Id}}", self.image, timeout=20).stdout.strip()
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None, "Invalid image identifier")
        self.note("engine", "ready", image_id=image, engine=docker if re.fullmatch(r"[a-zA-Z0-9._-]{1,50}", docker) else None)
        self.provision_network()
        create_tls(self.tls, self.fixture["addresses"])
        self.fixture["ca"] = (self.tls / "ca.crt").read_text()
        for peer in PEERS:
            name = self.name(peer)
            self.command("volume", "create", name)
            self.volumes.add(name)
            scoped = {**self.fixture, "tls": {
                "lan-proxy.crt": (self.tls / f"{peer}.crt").read_text(),
                "lan-proxy.key": (self.tls / f"{peer}.key").read_text(),
                "ca.crt": self.fixture["ca"],
            }}
            # Normal image entrypoint, uid and persistent volume. Own TLS/key
            # setup alone has no connectivity and no peer/trust bootstrap.
            transient = f"{name}-setup"
            self.transient.add(transient)
            self.command("run", "--rm", "-i", "--name", transient, "--pull=never", "--network", "none",
                         *self._app_args(peer), self.image, PYTHON, "-m",
                         "tools.federation_docker_peer", "prepare", peer, fixture=scoped)
            self.note("private-scratch", "passed", peer=peer, limit_mib=PEER_SCRATCH_MIB)
            self.command("create", "--pull=never", "--name", name, "--hostname", peer,
                         "--network", self.network, "--ip", self.fixture["addresses"][peer],
                         *self._app_args(peer), self.image)
            self.containers.add(name)
            self.command("start", name)
        require(len(self.volumes) == len(self.containers) == 2, "Two independent persistent peers required")

    def invoke(self, phase, peer=None):
        require(phase in START_STAGES, "Unknown fixture command")
        started = time.monotonic()
        self.note(phase, "running", **({"peer": peer} if peer else {}))
        if peer:
            args = ("exec", "-i", "-e", "PYTHONPATH=/run/test-python", self.name(peer), PYTHON,
                    "-m", "tools.federation_docker_peer", phase, peer)
            if phase == "outbound-during-outage":
                args = args[:-1]
        else:
            transient = self.prefix + "-" + phase
            self.transient.add(transient)
            args = ("run", "--rm", "-i", "--name", transient, "--pull=never", "--network", self.network, "--read-only",
                    "--user", APP_USER, "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                    *_scratch_args(CLIENT_SCRATCH_MIB), *self._deps(),
                    *_docker_env({"PYTHONPATH": "/run/test-python", **self._private_env()}),
                    "--entrypoint", PYTHON, self.image, "-m", "tools.federation_docker_peer", phase)
        result = self.command(*args, fixture=self.fixture, timeout=235, check=False)
        elapsed = round(time.monotonic() - started, 3)
        if result.returncode:
            detail = _failure(result)
            self.note(phase, "failed", elapsed_seconds=elapsed, **detail)
            raise RuntimeError(f"{phase} failed: {json.dumps(detail)}")
        try:
            answer = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"{phase} did not return fixture JSON") from exc
        self.note(phase, "passed", elapsed_seconds=elapsed,
                  scratch_limit_mib=PEER_SCRATCH_MIB if peer else CLIENT_SCRATCH_MIB,
                  **({"peer": peer} if peer else {}))
        return answer

    def restart(self):
        for peer in PEERS:
            name = self.name(peer)
            before = self.command("inspect", "--format", "{{.State.StartedAt}}", name).stdout.strip()
            self.command("restart", "--time", "15", name, timeout=35)
            after = self.command("inspect", "--format", "{{.State.StartedAt}}", name).stdout.strip()
            require(after and after != before, "Container restart did not replace the process")
            self.note("container-restart", "passed", peer=peer)

    def collect(self):
        for peer in PEERS:
            name = self.name(peer)
            state = {"peer": peer, "exists": name in self.containers}
            if name in self.containers:
                try:
                    status = self.command("inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}",
                                          name, check=False, timeout=15)
                    if re.fullmatch(r"[a-z]+ \d+", status.stdout.strip()):
                        state["container"] = status.stdout.strip()
                    logs = self.command("logs", "--timestamps", "--tail", "400", name, check=False, timeout=15)
                    with (self.output / f"{peer}.log.jsonl").open("w", encoding="utf-8") as stream:
                        for item in _events(logs.stdout + "\n" + logs.stderr):
                            print(json.dumps(item, sort_keys=True), file=stream)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    state["collector_error"] = type(exc).__name__
                if state.get("container", "").startswith("running "):
                    try:
                        state["application"] = self.invoke("status", peer)
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        state["application"] = {"error": "redacted snapshot unavailable"}
            (self.output / f"{peer}.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    def cleanup(self):
        tracked = [("rm", "-f", name) for name in self.transient | self.containers]
        tracked += [("volume", "rm", volume) for volume in self.volumes]
        if self.has_network:
            tracked.append(("network", "rm", self.network))
        errors = 0
        for command in tracked:
            try:
                result = self.command(*command, check=False, timeout=20)
                if result.returncode and not re.search(r"no such (container|volume|network)", result.stderr, re.I):
                    errors += 1
            except (OSError, subprocess.SubprocessError):
                errors += 1
        if errors:
            self.note("cleanup", "failed", resources=errors)
            raise RuntimeError("Disposable fixture cleanup did not complete")


def _docker_env(values):
    return tuple(item for key, value in values.items() for item in ("--env", f"{key}={value}"))


def validate_download_counts(snapshot, *receipts):
    rows = {item["job"]: item for item in snapshot["queue"]}
    events = snapshot["events"]
    for receipt in receipts:
        job = receipt["job"]
        selected = [event for event in events if event["job"] == job]
        finished = [event for event in selected if event["kind"] == "download_completed"]
        counts = [event.get("network_blocks", 0) for event in selected
                  if event.get("network_bytes") == SIZE]
        legacy_chunks = [event for event in selected if event["kind"] == "download_chunk_completed"]
        require(rows[job]["status"] == "complete" and len(finished) == 1, "Expected one completed receipt")
        require(any(count >= 2 for count in counts) or len(legacy_chunks) >= 2,
                "Expected multiple transferred blocks per synthetic document")


def run(deployment):
    fixture = deployment.fixture
    a, b = PEERS
    deployment.note("provision", "running")
    deployment.prepare()
    deployment.note("provision", "passed")
    for phase in ("pair", "initial"):
        fixture.update(deployment.invoke(phase))
    deployment.restart()
    deployment.invoke("after-restart")
    fixture.update(deployment.invoke("queue-outage"))
    deployment.invoke("stop-proxy", a)
    fixture.update(deployment.invoke("outbound-during-outage", a))
    fixture.update(deployment.invoke("waiting"))
    delayed = deployment.invoke("status", b)
    waiting = next(row for row in delayed["queue"] if row["job"] == fixture["waiting"]["job"])
    require(waiting["attempts"] == 1 and waiting["retry_at"] == fixture["waiting"]["retry_at"],
            "Early manual attempt did not respect exact retry")
    deployment.invoke("start-proxy", a)
    fixture.update(deployment.invoke("recovered"))
    final = {name: deployment.invoke("status", name) for name in PEERS}
    validate_download_counts(final[a], fixture["receipts"][a], fixture["outbound_receipt"])
    validate_download_counts(final[b], fixture["receipts"][b], fixture["recovered_receipt"])
    failed_id = fixture["waiting"]["job"]
    delayed = next(row for row in final[b]["queue"] if row["job"] == failed_id)
    require(delayed["attempts"] == 2, "Recovered queue item should have exactly two attempts")
    result = {name: fixture[name] for name in ("identities", "receipts", "waiting",
                                                "outbound_receipt", "recovered_receipt")}
    (deployment.output / "verified.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    deployment.note("complete", "passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Already built normal application image")
    parser.add_argument("--html-deps", type=Path, required=True, help="Disposable pure-Python bs4 dependency directory")
    parser.add_argument("--artifacts", type=Path, required=True, help="Directory for redacted evidence")
    options = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,190}", options.image), "Invalid local image reference")
    output = options.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if sys.platform != "linux" or any(not shutil.which(name) for name in ("docker", "openssl")):
        (output / "unavailable.txt").write_text("Real Linux Docker Engine and OpenSSL are required. No fallback run.\n")
        return 1
    deps = options.html_deps.resolve()
    require((deps / "bs4/__init__.py").is_file() and (deps / "soupsieve/__init__.py").is_file(),
            "Isolated HTML test dependencies missing")
    require("," not in str(deps), "Fixture bind directory cannot contain a comma")
    with tempfile.TemporaryDirectory(prefix="so-deployment-tls-") as directory:
        deployment = Deployment(options.image, deps, output, Path(directory))
        try:
            run(deployment)
        except (OSError, RuntimeError, ValueError, AssertionError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
            deployment.note("acceptance", "failed", error=type(exc).__name__)
            print(f"Federation Docker acceptance failed ({type(exc).__name__}): {exc}", file=sys.stderr)
            return 1
        finally:
            try:
                deployment.collect()
            finally:
                deployment.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
