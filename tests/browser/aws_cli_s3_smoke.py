#!/usr/bin/env python3
"""Real AWS CLI S3 smoke against the isolated browser-test container."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

OUT = Path("test-results/browser/s3-aws-cli.json")
ENDPOINT = "http://127.0.0.1:8080/s3"
BUCKET = "simpleoffice"


def run(command, *, env=None, expected=0):
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=90)
    if (result.returncode == 0) != (expected == 0):
        # Include only the S3 error code; never log raw stderr or credentials.
        import re
        error = re.search(r"\(([^()]{1,80})\) when calling the", result.stderr)
        error_code = error.group(1) if error else "unknown"
        # Classify transport failures without logging raw stderr or credentials.
        stderr = result.stderr.lower()
        if error_code == "unknown":
            for marker, classification in (
                ("could not connect to the endpoint url", "EndpointConnectionError"),
                ("connection was closed", "ConnectionClosedError"),
                ("ssl validation failed", "SSLError"),
                ("failed to connect to proxy url", "ProxyConnectionError"),
                ("invalid endpoint", "InvalidEndpoint"),
            ):
                if marker in stderr:
                    error_code = classification
                    break
        raise RuntimeError(f"AWS CLI operation failed (exit={result.returncode}, expected={expected}, s3_error={error_code})")
    return result


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = {"client": "aws-cli", "commit": os.getenv("GITHUB_SHA", "local"),
              "environment": "GitHub Actions ubuntu-24.04, isolated Docker peer A",
              "checks": [], "status": "failed"}
    try:
        version = run(["aws", "--version"])
        report["client_version"] = (version.stdout or version.stderr).strip()
        setup = """
import json
from app import app
from app.db import ensure_auth_database, get_db
from app.s3_overlay import credentials
with app.app_context():
    ensure_auth_database()
    db = get_db()
    db.execute("INSERT OR IGNORE INTO user(username,password,is_admin,is_disabled,auth_version) VALUES(?,?,?,?,?)", ("s3-smoke-user", "disabled-smoke-login", 0, 0, 1))
    db.commit()
    pair = credentials.create("s3-smoke-user", "ephemeral-aws-cli-smoke", ["read", "inbox:put"], "", 1)
    print(json.dumps({"access": pair["access_key"], "secret": pair["secret_key"]}))
"""
        pair = json.loads(run(["docker", "exec", "simpleoffice-browser-peer-a",
                               "python", "-c", setup]).stdout.strip().splitlines()[-1])
        env = {**os.environ, "AWS_ACCESS_KEY_ID": pair["access"],
               "AWS_SECRET_ACCESS_KEY": pair["secret"],
               "AWS_DEFAULT_REGION": "us-east-1",
               "AWS_EC2_METADATA_DISABLED": "true",
               "AWS_MAX_ATTEMPTS": "1"}
        def aws(*args, expected=0):
            return run(["aws", "--endpoint-url", ENDPOINT, "--no-cli-pager",
                        *args], env=env, expected=expected)
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "aws-config"
            config.write_text("[default]\ns3 =\n    addressing_style = path\n")
            env["AWS_CONFIG_FILE"] = str(config)
            env["AWS_SHARED_CREDENTIALS_FILE"] = str(Path(tmp) / "empty-credentials")
            source = Path(tmp) / "source.bin"
            target = Path(tmp) / "download.bin"
            payload = b"SimpleOffice4Me AWS CLI external smoke synthetic fixture\n"
            source.write_bytes(payload)
            key = f"inbox/aws-cli-{os.getenv('GITHUB_RUN_ID', 'local')}.bin"
            aws("s3api", "list-buckets")
            report["checks"].append("list-buckets")
            aws("s3api", "list-objects-v2", "--bucket", BUCKET)
            report["checks"].append("list-objects-v2")
            aws("s3api", "put-object", "--bucket", BUCKET, "--key", key, "--body", str(source))
            report["checks"].append("put-object")
            aws("s3api", "head-object", "--bucket", BUCKET, "--key", key)
            report["checks"].append("head-object")
            aws("s3api", "get-object", "--bucket", BUCKET, "--key", key, str(target))
            if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(payload).digest():
                raise RuntimeError("GET content hash mismatch")
            report["checks"].append("get-object-sha256")
            aws("s3api", "get-object", "--bucket", BUCKET, "--key", key,
                "--range", "bytes=0-9", str(target))
            if target.read_bytes() != payload[:10]:
                raise RuntimeError("Range content mismatch")
            report["checks"].append("range-get")
            aws("s3api", "put-object", "--bucket", BUCKET, "--key", key, "--body", str(source))
            report["checks"].append("idempotent-put")
            source.write_bytes(b"different synthetic payload\n")
            aws("s3api", "put-object", "--bucket", BUCKET, "--key", key,
                "--body", str(source), expected=1)
            report["checks"].append("conflicting-put-rejected")
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        OUT.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
