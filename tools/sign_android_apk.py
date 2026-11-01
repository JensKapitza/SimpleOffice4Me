"""Finalize a signed APK with explicit v1 and v2 schemes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


STORE_PASSWORD_ENV = "SIMPLEOFFICE_RELEASE_KEYSTORE_PASSWORD"
KEY_PASSWORD_ENV = "SIMPLEOFFICE_RELEASE_KEY_PASSWORD"
SIGNING_TIMEOUT_SECONDS = 180


def _checked_apksigner_run(
    command: list[str],
    *,
    action: str,
    secrets: tuple[str, ...],
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SIGNING_TIMEOUT_SECONDS,
            env=os.environ.copy() if environment is None else environment,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"apksigner {action} timed out") from None
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        for secret in secrets:
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
        message = detail[:1000] if detail else "no diagnostic output"
        raise RuntimeError(f"apksigner {action} failed: {message}")
    return result


def finalize_apk(
    apk_path: str | Path,
    apksigner_path: str | Path,
    keystore_path: str | Path,
    key_alias: str,
) -> None:
    """Atomically replace an APK only after v1/v2 signing and verification."""
    apk = Path(apk_path).expanduser().resolve(strict=True)
    apksigner = Path(apksigner_path).expanduser().resolve(strict=True)
    keystore = Path(keystore_path).expanduser().resolve(strict=True)
    if not apk.is_file() or not apksigner.is_file() or not keystore.is_file():
        raise ValueError("APK, apksigner, and keystore must be readable files")
    if not os.access(apksigner, os.X_OK):
        raise ValueError("apksigner is not executable")
    if not key_alias.strip():
        raise ValueError("release signing alias is missing")

    store_password = os.environ.get(STORE_PASSWORD_ENV, "")
    key_password = os.environ.get(KEY_PASSWORD_ENV, "")
    if not store_password or not key_password:
        raise ValueError("release signing password environment is incomplete")

    original_mode = apk.stat().st_mode & 0o777
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{apk.name}.", suffix=".signed", dir=apk.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()
    secrets = (store_password, key_password)
    try:
        _checked_apksigner_run(
            [
                str(apksigner),
                "sign",
                "--v1-signing-enabled",
                "true",
                "--v2-signing-enabled",
                "true",
                "--ks",
                str(keystore),
                "--ks-type",
                "JKS",
                "--ks-key-alias",
                key_alias,
                "--ks-pass",
                f"env:{STORE_PASSWORD_ENV}",
                "--key-pass",
                f"env:{KEY_PASSWORD_ENV}",
                "--out",
                str(temporary),
                str(apk),
            ],
            action="sign",
            secrets=secrets,
        )
        verification_environment = os.environ.copy()
        verification_environment.pop(STORE_PASSWORD_ENV, None)
        verification_environment.pop(KEY_PASSWORD_ENV, None)
        verification = _checked_apksigner_run(
            [
                str(apksigner),
                "verify",
                "--verbose",
                "--print-certs",
                str(temporary),
            ],
            action="verify",
            secrets=secrets,
            environment=verification_environment,
        )
        output = f"{verification.stdout}\n{verification.stderr}"
        required = (
            "Verified using v1 scheme (JAR signing): true",
            "Verified using v2 scheme (APK Signature Scheme v2): true",
        )
        v1_verified = required[0] in output
        v2_verified = required[1] in output
        if not v1_verified or not v2_verified:
            raise RuntimeError(
                "final APK does not verify with both v1 and v2 schemes "
                f"(v1={v1_verified}, v2={v2_verified})"
            )
        os.chmod(temporary, original_mode)
        os.replace(temporary, apk)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, type=Path)
    parser.add_argument("--apksigner", required=True, type=Path)
    parser.add_argument("--keystore", required=True, type=Path)
    parser.add_argument("--key-alias", required=True)
    args = parser.parse_args(argv)
    try:
        finalize_apk(args.apk, args.apksigner, args.keystore, args.key_alias)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Android APK signing failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
