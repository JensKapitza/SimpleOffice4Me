#!/usr/bin/env python3
"""CI-only real LiteLLM container acceptance with a non-billable mock model."""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def ready(service, gateway, *, expected=True):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = service.action("scan")
        if bool(result["health"]["ok"]) == expected:
            return
        time.sleep(1)
    print("Last gateway diagnostic:", result["health"]["code"])
    ids = service._docker(["ps", "-aq", "--filter", "label=com.docker.compose.project=" + service._project(), "--format", "{{.ID}}"]).decode().split()
    for ident in ids:
        state = service._docker(["inspect", "--format", "{{json .State}}", ident]).decode()
        print("CI container state:", state)
        logged = subprocess.run(["docker", "logs", "--tail", "60", ident],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5, check=False)
        logs = logged.stdout.decode(errors="replace")
        for key in ("api_key", "provider_key"):
            logs = logs.replace(service.secret(service.settings(), key), "[redacted]")
        print(re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", logs))
    raise RuntimeError("LiteLLM container readiness gate failed")


def main():
    with tempfile.TemporaryDirectory(prefix="litellm-acceptance-") as temporary:
        os.environ["SIMPLEOFFICE_MINI_SERVICES_CONFIG"] = str(Path(temporary) / "mini-services.json")
        os.environ["SIMPLEOFFICE_DOCUMENT_ROOT"] = str(Path(temporary) / "documents")
        from app import app
        from app import litellm_service as service
        from app import litellm_config as config
        from app import litellm_gateway as gateway
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        original_compose = service._compose

        def mock_compose(settings):
            path = original_compose(settings)
            compose = json.loads(path.read_text())
            compose["services"]["gateway"]["logging"] = {"driver": "local"}
            path.write_text(json.dumps(compose))
            target = service.directory() / "config.yaml"
            data = json.loads(target.read_text())
            data["model_list"][0]["litellm_params"]["mock_response"] = "CI_GATEWAY_OK"
            target.write_text(json.dumps(data))
            return path

        with app.app_context(), patch.object(service, "_compose", side_effect=mock_compose):
            service.save_settings({"enabled": True, "mode": "local", "port": port,
                                   "model": "ci-model", "provider_model": "openai/ci-model",
                                   "api_key": "sk-" + secrets.token_hex(32), "provider_key": "sk-" + secrets.token_hex(32),
                                   "timeout": 2, "retries": 0})
            try:
                service.action("install")
                service.action("start")
                ready(service, gateway)
                result = gateway.completion([{"role": "user", "content": "non-billable test"}])
                if result["choices"][0]["message"]["content"] != "CI_GATEWAY_OK":
                    raise RuntimeError("OpenAI completion gate failed")
                saved = json.loads(service.backup())
                candidate = {**config.settings(), "api_key_enc": config.prepare({"api_key": "sk-" + secrets.token_hex(32)})["api_key_enc"]}
                if gateway.probe(candidate)["code"] != "unauthorized":
                    raise RuntimeError("Invalid key gate failed")
                service.action("restart")
                ready(service, gateway)
                service.save_settings({"enabled": False})
                if service._running():
                    raise RuntimeError("Deactivation left a running container")
                service.restore(saved)
                if config.settings()["enabled"]:
                    raise RuntimeError("Restore unexpectedly enabled the gateway")
                service.save_settings({"enabled": True})
                service.action("start")
                ready(service, gateway)
                # Reinstall the same pinned image while stopped: upgrade preparation is idempotent.
                service.action("stop")
                service.action("install")
                service.action("start")
                ready(service, gateway)
            finally:
                service.action("stop")
    print("LiteLLM real container gates passed: install, readiness, completion, authentication, restart, backup, restore, disable, reinstall.")


if __name__ == "__main__":
    main()
