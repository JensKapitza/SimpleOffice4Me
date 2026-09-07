"""Encrypted file packages for private sharing and untrusted federation storage.

Files are encrypted before they enter federation.  The package header contains
only cryptographic parameters; file names and archive metadata live inside the
encrypted tar stream.  Packages can be unlocked either with an out-of-band
password or a randomly generated recovery key.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import struct
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"SOVP1\x00"
FORMAT = "simpleoffice-vault-package"
VERSION = 1
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
MIN_CHUNK_SIZE = 64 * 1024
MAX_CHUNK_SIZE = 16 * 1024 * 1024
MAX_HEADER_SIZE = 64 * 1024
MAX_RECORD_SIZE = MAX_CHUNK_SIZE + 1024 * 1024
KDF_N = 2**15
KDF_R = 8
KDF_P = 1
FOOTER_INDEX = 0xFFFFFFFF


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise ValueError("Ungültige Base64-Daten") from exc


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _derive_password_key(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 4096:
        raise ValueError("Paket-Passwort muss zwischen 12 und 4096 Zeichen lang sein")
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def _recovery_key_text(key: bytes) -> str:
    return _b64(key).rstrip("=")


def _recovery_key_bytes(value: str) -> bytes:
    text = str(value or "").strip()
    padding = "=" * ((4 - len(text) % 4) % 4)
    raw = _unb64(text + padding)
    if len(raw) != 32:
        raise ValueError("Ungültiger Wiederherstellungsschlüssel")
    return raw


def _nonce(prefix: bytes, index: int) -> bytes:
    if len(prefix) != 8 or not 0 <= index <= FOOTER_INDEX:
        raise ValueError("Ungültiger Paket-Nonce")
    return prefix + struct.pack(">I", index)


def _aad(package_id: str, index: int) -> bytes:
    label = "footer" if index == FOOTER_INDEX else str(index)
    return f"simpleoffice-vault-package:v1:{package_id}:{label}".encode("utf-8")


def _write_record(target: BinaryIO, payload: bytes) -> None:
    if not payload or len(payload) > MAX_RECORD_SIZE:
        raise ValueError("Ungültige Paket-Record-Größe")
    target.write(struct.pack(">I", len(payload)))
    target.write(payload)


def _read_record(source: BinaryIO) -> bytes | None:
    header = source.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("Paket ist abgeschnitten")
    length = struct.unpack(">I", header)[0]
    if length < 16 or length > MAX_RECORD_SIZE:
        raise ValueError("Ungültige Paket-Record-Größe")
    payload = source.read(length)
    if len(payload) != length:
        raise ValueError("Paket ist abgeschnitten")
    return payload


def _safe_archive_name(name: str) -> str:
    path = PurePosixPath(str(name).replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Unsicherer Dateiname für Vault-Paket")
    clean = "/".join(part for part in path.parts if part not in {"", "."})
    if not clean or len(clean) > 4096:
        raise ValueError("Ungültiger Dateiname für Vault-Paket")
    return clean


class _EncryptingWriter(io.RawIOBase):
    def __init__(self, target: BinaryIO, key: bytes, prefix: bytes, package_id: str, chunk_size: int):
        self.target = target
        self.key = key
        self.prefix = prefix
        self.package_id = package_id
        self.chunk_size = chunk_size
        self.buffer = bytearray()
        self.index = 0
        self.total = 0
        self.digest = hashlib.sha256()
        self.closed_package = False

    def writable(self) -> bool:
        return True

    def write(self, data: bytes | bytearray) -> int:
        if self.closed_package:
            raise ValueError("Vault-Paket ist bereits abgeschlossen")
        raw = bytes(data)
        self.buffer.extend(raw)
        while len(self.buffer) >= self.chunk_size:
            chunk = bytes(self.buffer[: self.chunk_size])
            del self.buffer[: self.chunk_size]
            self._emit(chunk)
        return len(raw)

    def flush(self) -> None:
        if hasattr(self.target, "flush"):
            self.target.flush()

    def _emit(self, chunk: bytes) -> None:
        cipher = AESGCM(self.key).encrypt(_nonce(self.prefix, self.index), chunk, _aad(self.package_id, self.index))
        _write_record(self.target, cipher)
        self.digest.update(chunk)
        self.total += len(chunk)
        self.index += 1
        if self.index >= FOOTER_INDEX:
            raise ValueError("Vault-Paket enthält zu viele Chunks")

    def finish(self) -> dict[str, object]:
        if self.closed_package:
            raise ValueError("Vault-Paket wurde bereits abgeschlossen")
        if self.buffer:
            self._emit(bytes(self.buffer))
            self.buffer.clear()
        footer = {
            "chunk_count": self.index,
            "plaintext_size": self.total,
            "plaintext_sha256": self.digest.hexdigest(),
        }
        encrypted_footer = AESGCM(self.key).encrypt(
            _nonce(self.prefix, FOOTER_INDEX), _canonical(footer), _aad(self.package_id, FOOTER_INDEX)
        )
        _write_record(self.target, encrypted_footer)
        self.closed_package = True
        self.flush()
        return footer


class _DecryptingReader(io.RawIOBase):
    def __init__(self, source: BinaryIO, key: bytes, prefix: bytes, package_id: str):
        self.source = source
        self.key = key
        self.prefix = prefix
        self.package_id = package_id
        self.pending = _read_record(source)
        if self.pending is None:
            raise ValueError("Vault-Paket enthält keine Daten")
        self.buffer = bytearray()
        self.index = 0
        self.total = 0
        self.digest = hashlib.sha256()
        self.finished = False
        self.footer: dict[str, object] | None = None

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        if not self.buffer and not self.finished:
            self._fill()
        if not self.buffer:
            return 0
        amount = min(len(b), len(self.buffer))
        b[:amount] = self.buffer[:amount]
        del self.buffer[:amount]
        return amount

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        if size < 0:
            parts = [bytes(self.buffer)]
            self.buffer.clear()
            while not self.finished:
                self._fill()
                parts.append(bytes(self.buffer))
                self.buffer.clear()
            return b"".join(parts)
        while len(self.buffer) < size and not self.finished:
            self._fill()
        out = bytes(self.buffer[:size])
        del self.buffer[:size]
        return out

    def _fill(self) -> None:
        if self.finished:
            return
        current = self.pending
        following = _read_record(self.source)
        if following is None:
            try:
                raw = AESGCM(self.key).decrypt(
                    _nonce(self.prefix, FOOTER_INDEX), current, _aad(self.package_id, FOOTER_INDEX)
                )
                footer = json.loads(raw)
            except (InvalidTag, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("Vault-Paket ist beschädigt oder der Schlüssel ist falsch") from exc
            if not isinstance(footer, dict):
                raise ValueError("Ungültiger Vault-Paket-Footer")
            if int(footer.get("chunk_count", -1)) != self.index:
                raise ValueError("Vault-Paket ist unvollständig")
            if int(footer.get("plaintext_size", -1)) != self.total:
                raise ValueError("Vault-Paket-Größe stimmt nicht")
            if str(footer.get("plaintext_sha256", "")) != self.digest.hexdigest():
                raise ValueError("Vault-Paket-Prüfsumme stimmt nicht")
            self.footer = footer
            self.finished = True
            return
        try:
            plain = AESGCM(self.key).decrypt(
                _nonce(self.prefix, self.index), current, _aad(self.package_id, self.index)
            )
        except InvalidTag as exc:
            raise ValueError("Vault-Paket ist beschädigt oder der Schlüssel ist falsch") from exc
        self.pending = following
        self.index += 1
        self.total += len(plain)
        self.digest.update(plain)
        self.buffer.extend(plain)


def _write_header(target: BinaryIO, header: dict[str, object]) -> None:
    raw = _canonical(header)
    if len(raw) > MAX_HEADER_SIZE:
        raise ValueError("Vault-Paket-Header ist zu groß")
    target.write(MAGIC)
    target.write(struct.pack(">I", len(raw)))
    target.write(raw)


def _read_header(source: BinaryIO) -> dict[str, object]:
    if source.read(len(MAGIC)) != MAGIC:
        raise ValueError("Kein SimpleOffice Vault-Paket")
    raw_len = source.read(4)
    if len(raw_len) != 4:
        raise ValueError("Vault-Paket-Header fehlt")
    length = struct.unpack(">I", raw_len)[0]
    if length < 2 or length > MAX_HEADER_SIZE:
        raise ValueError("Ungültiger Vault-Paket-Header")
    raw = source.read(length)
    if len(raw) != length:
        raise ValueError("Vault-Paket-Header ist abgeschnitten")
    try:
        header = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Ungültiger Vault-Paket-Header") from exc
    if not isinstance(header, dict) or header.get("format") != FORMAT or header.get("version") != VERSION:
        raise ValueError("Nicht unterstütztes Vault-Paket")
    return header


def create_vault_package(
    files: Iterable[str | Path],
    output: str | Path,
    *,
    password: str | None = None,
    recovery_key: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, object]:
    """Create an encrypted package. Returns an out-of-band recovery key when generated."""
    if password and recovery_key:
        raise ValueError("Entweder Passwort oder Wiederherstellungsschlüssel verwenden")
    if not MIN_CHUNK_SIZE <= int(chunk_size) <= MAX_CHUNK_SIZE:
        raise ValueError("Ungültige Paket-Chunk-Größe")
    sources: list[tuple[Path, str]] = []
    used_names: set[str] = set()
    for value in files:
        path = Path(value).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Nur reguläre Dateien können gepackt werden: {path}")
        path = path.resolve()
        name = _safe_archive_name(path.name)
        if name in used_names:
            raise ValueError(f"Doppelter Dateiname im Vault-Paket: {name}")
        used_names.add(name)
        sources.append((path, name))
    if not sources:
        raise ValueError("Mindestens eine Datei ist erforderlich")

    package_id = secrets.token_hex(16)
    nonce_prefix = secrets.token_bytes(8)
    content_key = secrets.token_bytes(32)
    generated_recovery_key = ""
    header: dict[str, object] = {
        "format": FORMAT,
        "version": VERSION,
        "package_id": package_id,
        "chunk_size": int(chunk_size),
        "nonce_prefix": _b64(nonce_prefix),
    }
    if password:
        salt = secrets.token_bytes(16)
        wrap_key = _derive_password_key(password, salt, n=KDF_N, r=KDF_R, p=KDF_P)
        wrap_nonce = secrets.token_bytes(12)
        wrapped_key = AESGCM(wrap_key).encrypt(
            wrap_nonce, content_key, f"{FORMAT}:wrap:v1:{package_id}".encode("utf-8")
        )
        header["unlock"] = {
            "mode": "password", "salt": _b64(salt), "kdf": "scrypt",
            "n": KDF_N, "r": KDF_R, "p": KDF_P,
            "wrap_nonce": _b64(wrap_nonce), "wrapped_key": _b64(wrapped_key),
        }
    else:
        if recovery_key:
            content_key = _recovery_key_bytes(recovery_key)
        else:
            generated_recovery_key = _recovery_key_text(content_key)
        header["unlock"] = {"mode": "recovery_key"}

    target_path = Path(output).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and target_path.is_symlink():
        raise ValueError("Vault-Paket-Ziel darf kein Symlink sein")
    tmp = target_path.with_name(target_path.name + ".tmp")
    footer: dict[str, object] | None = None
    try:
        with tmp.open("wb") as target:
            _write_header(target, header)
            writer = _EncryptingWriter(target, content_key, nonce_prefix, package_id, int(chunk_size))
            with tarfile.open(fileobj=writer, mode="w|") as archive:
                for path, name in sources:
                    info = archive.gettarinfo(str(path), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o600
                    with path.open("rb") as source:
                        archive.addfile(info, source)
            footer = writer.finish()
        os.replace(tmp, target_path)
        if os.name == "posix":
            os.chmod(target_path, 0o600)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return {
        "package_id": package_id,
        "path": str(target_path),
        "files": len(sources),
        "unlock_mode": str(header["unlock"]["mode"]),
        "recovery_key": generated_recovery_key,
        "plaintext_size": int((footer or {}).get("plaintext_size", 0)),
        "chunk_count": int((footer or {}).get("chunk_count", 0)),
    }


def _content_key(header: dict[str, object], *, password: str | None, recovery_key: str | None) -> bytes:
    unlock = header.get("unlock")
    if not isinstance(unlock, dict):
        raise ValueError("Vault-Paket enthält keine Entsperrinformation")
    mode = str(unlock.get("mode") or "")
    package_id = str(header.get("package_id") or "")
    if mode == "recovery_key":
        if password or not recovery_key:
            raise ValueError("Wiederherstellungsschlüssel erforderlich")
        return _recovery_key_bytes(recovery_key)
    if mode != "password" or recovery_key or not password:
        raise ValueError("Paket-Passwort erforderlich")
    try:
        salt = _unb64(str(unlock["salt"]))
        wrap_nonce = _unb64(str(unlock["wrap_nonce"]))
        wrapped_key = _unb64(str(unlock["wrapped_key"]))
        key = _derive_password_key(
            password, salt, n=int(unlock["n"]), r=int(unlock["r"]), p=int(unlock["p"])
        )
        return AESGCM(key).decrypt(
            wrap_nonce, wrapped_key, f"{FORMAT}:wrap:v1:{package_id}".encode("utf-8")
        )
    except (KeyError, TypeError, ValueError, InvalidTag) as exc:
        raise ValueError("Paket-Passwort ist falsch oder das Paket wurde verändert") from exc


def extract_vault_package(
    package: str | Path,
    destination: str | Path,
    *,
    password: str | None = None,
    recovery_key: str | None = None,
) -> dict[str, object]:
    """Decrypt and safely extract regular files without writing a plaintext temp archive."""
    package_path = Path(package).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    destination_path.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with package_path.open("rb") as source:
        header = _read_header(source)
        key = _content_key(header, password=password, recovery_key=recovery_key)
        prefix = _unb64(str(header.get("nonce_prefix") or ""))
        package_id = str(header.get("package_id") or "")
        if len(prefix) != 8 or not package_id:
            raise ValueError("Vault-Paket-Header ist beschädigt")
        reader = _DecryptingReader(source, key, prefix, package_id)
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            for member in archive:
                name = _safe_archive_name(member.name)
                if member.isdir():
                    continue
                if not member.isfile() or member.issym() or member.islnk() or member.isdev():
                    raise ValueError("Vault-Paket enthält einen nicht erlaubten Dateityp")
                target = (destination_path / name).resolve()
                if destination_path not in target.parents:
                    raise ValueError("Unsicherer Pfad im Vault-Paket")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.is_symlink():
                    raise ValueError("Zieldatei darf kein Symlink sein")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("Vault-Paket-Datei kann nicht gelesen werden")
                with target.open("wb") as output:
                    while True:
                        block = stream.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                if os.name == "posix":
                    os.chmod(target, 0o600)
                extracted.append(name)
        # tarfile may stop at archive EOF before asking the reader for the encrypted footer.
        while not reader.finished:
            reader.read(1024 * 1024)
    return {"package_id": package_id, "files": len(extracted), "extracted": extracted}


def federation_chunk_plan(chunk_count: int, peers: list[str], replicas: int = 2) -> dict[str, list[int]]:
    """Round-robin encrypted chunks across peers without giving every peer a full copy."""
    clean_peers = list(dict.fromkeys(str(peer).strip() for peer in peers if str(peer).strip()))
    if chunk_count < 0 or not clean_peers:
        raise ValueError("Ungültiger Federation-Backup-Plan")
    replicas = int(replicas)
    if not 1 <= replicas <= len(clean_peers):
        raise ValueError("Replikate müssen zwischen 1 und Anzahl der Peers liegen")
    result = {peer: [] for peer in clean_peers}
    for index in range(int(chunk_count)):
        for offset in range(replicas):
            peer = clean_peers[(index + offset) % len(clean_peers)]
            result[peer].append(index)
    return result


def package_capabilities() -> dict[str, object]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "encrypt_before_federation": True,
        "encrypted_metadata": True,
        "unlock_modes": ["password", "recovery_key"],
        "federation_partial_storage": True,
        "erasure_coding": "planned",
    }
