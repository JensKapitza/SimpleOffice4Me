"""Storage path helpers for business documents."""
from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path, PureWindowsPath

from .business_document_common import INVOICE_DIR, LINK_FILE, TEMPLATE_DIR
from .document_store import CONTROL_DIR
from .file_lock import exclusive_file_lock
from .safe_paths import resolve_for_write_under


_CONTACT_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,199}")
_WINDOWS_DEVICE = re.compile(r"CON|PRN|AUX|NUL|(?:COM|LPT)[0-9]", re.IGNORECASE)
_GENERATED_KINDS = {"invoice", "letter", "credit_note"}


def _generated_contact_component(contact_id: str) -> str:
    portable = contact_id.replace("\\", "/")
    if (
        not contact_id
        or "\x00" in contact_id
        or portable.startswith("/")
        or PureWindowsPath(contact_id).drive
        or any(part in {".", ".."} for part in portable.split("/"))
    ):
        raise ValueError("invalid contact ID for generated documents")
    if (
        _CONTACT_COMPONENT.fullmatch(contact_id)
        and not contact_id.endswith(".")
        and not _WINDOWS_DEVICE.fullmatch(contact_id.partition(".")[0])
    ):
        return contact_id
    # Older contacts can have opaque UIDs. The leading tilde keeps their
    # storage names distinct from IDs carried through directly.
    digest = hashlib.sha256(contact_id.encode("utf-8")).hexdigest()
    return f"~uid-{digest}"


def _pin_windows_directory(path: Path, stack: ExitStack) -> None:
    """Hold a plain directory open against reparse changes and renames."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL

    list_directory = 0x0001
    read_attributes = 0x0080
    share_read = 0x0001
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    file_attribute_tag_info = 9
    # Attribute-only access does not enforce sharing restrictions on Windows.
    handle = kernel.CreateFileW(
        str(path), list_directory | read_attributes, share_read, None, open_existing,
        backup_semantics | open_reparse_point, None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    stack.callback(kernel.CloseHandle, handle)
    attribute_tag = (wintypes.DWORD * 2)()
    if not kernel.GetFileInformationByHandleEx(
        handle, file_attribute_tag_info, attribute_tag, ctypes.sizeof(attribute_tag),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = attribute_tag[0]
    if not attributes & stat.FILE_ATTRIBUTE_DIRECTORY or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("generated document directories must not be links")


@contextmanager
def write_generated_pdf(root: Path, kind: str, year: str, contact_id: str, filename: str, pdf: bytes) -> Iterator[Path]:
    """Create a PDF and hold the DocumentStore lock until registration finishes.

    Filesystem actors that bypass the application lock can still mutate paths.
    """
    if kind not in _GENERATED_KINDS or not re.fullmatch(r"[0-9]{4}", year):
        raise ValueError("invalid generated document kind or year")
    if not re.fullmatch(r"[0-9][A-Za-z0-9._-]+\.pdf", filename):
        raise ValueError("invalid generated document filename")
    components = ("generated", kind, year, _generated_contact_component(contact_id))
    root = root.resolve(strict=True)
    target = root.joinpath(*components, filename)
    relative_lock = Path(CONTROL_DIR) / ".document-content.lock"
    lock_path = root / relative_lock
    if resolve_for_write_under(root, relative_lock) != lock_path:
        raise ValueError("document content lock must not be redirected")
    with exclusive_file_lock(lock_path), ExitStack() as directories:
        if os.name == "nt":
            parent = root
            _pin_windows_directory(parent, directories)
            for component in components:
                parent /= component
                try:
                    parent.mkdir()
                except FileExistsError:
                    pass
                _pin_windows_directory(parent, directories)
            with target.open("xb") as handle:
                handle.write(pdf)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            parent_fd = os.open(root, flags)
            directories.callback(os.close, parent_fd)
            for component in components:
                try:
                    os.mkdir(component, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                try:
                    parent_fd = os.open(component, flags, dir_fd=parent_fd)
                except OSError as exc:
                    if exc.errno not in {errno.ELOOP, errno.ENOTDIR}:
                        raise
                    raise ValueError("generated document directories must not be links") from exc
                directories.callback(os.close, parent_fd)
            file_fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666, dir_fd=parent_fd)
            with os.fdopen(file_fd, "wb") as handle:
                handle.write(pdf)
        yield target


def template_directory(root: Path) -> Path:
    path = root / CONTROL_DIR / TEMPLATE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def template_index(root: Path) -> Path:
    return template_directory(root) / "templates.json"


def contact_link_path(root: Path) -> Path:
    path = root / CONTROL_DIR / LINK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def invoice_store_path(root: Path, invoice_id: str) -> Path:
    path = root / CONTROL_DIR / INVOICE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{invoice_id}.json"
