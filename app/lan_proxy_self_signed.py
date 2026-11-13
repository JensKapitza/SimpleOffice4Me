"""Generate a local, self-signed TLS identity for the HTTPS LAN proxy.

The certificate is not automatically trusted by other devices.
"""
from __future__ import annotations

import ipaddress
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .lan_proxy_config import lan_proxy_tls_directory

CERT_NAME = "lan-proxy-self-signed.crt"
KEY_NAME = "lan-proxy-self-signed.key"


def ensure_self_signed_certificate(bind_host: str) -> tuple[str, str]:
    """Create a persistent identity if missing; never silently replace one half."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    address = ipaddress.ip_address(bind_host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Automatic TLS requires a specific LAN or loopback IP.")
    directory = lan_proxy_tls_directory()
    cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
    if cert_path.is_symlink() or key_path.is_symlink():
        raise ValueError("TLS identity paths must not be symbolic links.")
    if cert_path.exists() != key_path.exists():
        raise ValueError("Incomplete TLS identity: remove or repair it manually.")
    if cert_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if x509.IPAddress(address) not in cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value:
            raise ValueError("Existing TLS certificate does not cover the configured IP.")
        if cert.not_valid_after.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc) + timedelta(days=7):
            raise ValueError("Existing TLS certificate expires soon; renew explicitly.")
        return CERT_NAME, KEY_NAME

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SimpleOffice LAN")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
    created = []
    try:
        for path, data in ((key_path, key_bytes), (cert_path, cert_bytes)):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
        return CERT_NAME, KEY_NAME
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
