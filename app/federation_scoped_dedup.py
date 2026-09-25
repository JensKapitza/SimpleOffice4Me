"""Client-side V2 scoped block reuse without stable remote block hashes."""
from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

from .federation_blocks import FederationBlockStore, sha512_bytes
from .federation_core import preallocate, verify_file
from .federation_local_profile import local_peer_id
from .federation_peer_auth import headers as peer_auth_headers
from .federation_store import FederationStore
from .federation_worker import _request
from .v2.scoped_dedup import scoped_block_token, scoped_manifest_valid


def _signed_get(
    root: str | Path,
    peer: dict[str, Any],
    token: str,
    path: str,
    *,
    timeout: int = 120,
):
    source_token = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not source_token:
        raise ValueError("Lokaler Federation-Token fehlt für peer-signiertes V2-Dedup")
    headers = peer_auth_headers(
        local_peer_id(),
        source_token,
        "GET",
        path,
        b"",
    )
    return _request(
        peer["base_url"] + path,
        token=token,
        headers=headers,
        timeout=timeout,
    )


def _remote_manifest(
    root: str | Path,
    request_row: dict[str, Any],
    peer: dict[str, Any],
    token: str,
) -> dict[str, Any] | None:
    remote_document_id = str(request_row.get("remote_document_id") or "").strip()
    blob_hash = str(request_row.get("blob_hash") or "").strip()
    if not remote_document_id:
        raise ValueError("V2-Dedup benötigt eine konkrete Remote-Dokument-ID")
    path = (
        "/federation/v2/blocks/documents/"
        + urllib.parse.quote(remote_document_id, safe="")
        + "/manifest"
    )
    try:
        with _signed_get(root, peer, token, path) as response:
            raw = response.read()
        manifest = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 405, 501, 503}:
            return None
        raise
    if not isinstance(manifest, dict):
        raise ValueError("Peer liefert kein V2-Dedup-Manifest")
    if str(manifest.get("document_id") or "") != remote_document_id:
        raise ValueError("Peer liefert ein V2-Dedup-Manifest für ein anderes Dokument")
    if not scoped_manifest_valid(manifest, shared_secret=token, blob_hash=blob_hash):
        raise ValueError("Peer liefert ein ungültiges V2-Dedup-Manifest")
    return manifest


def scoped_deduplicated_download(
    root: str | Path,
    request_row: dict[str, Any],
    peer: dict[str, Any],
    token: str,
    federation: FederationStore,
) -> Path | None:
    manifest = _remote_manifest(root, request_row, peer, token)
    if manifest is None:
        return None

    block_store = FederationBlockStore(root)
    index_result = block_store.index_documents()
    session = str(manifest["session"])
    needed = {str(block["token"]) for block in manifest["blocks"]}
    local = block_store.match_scoped_tokens(
        needed,
        lambda digest: scoped_block_token(token, session, digest),
    )

    partial = federation.incoming / f"pull-scoped-{request_row['request_id']}.part"
    preallocate(partial, int(manifest["size"]))
    local_bytes = network_bytes = local_blocks = network_blocks = 0

    with partial.open("r+b") as target:
        for block in manifest["blocks"]:
            proof = str(block["token"])
            length = int(block["length"])
            digest = local.get(proof)
            if digest:
                data = block_store.read_block(digest, length)
                local_blocks += 1
                local_bytes += length
            else:
                query = urllib.parse.urlencode({"session": session, "proof": proof})
                path = (
                    "/federation/v2/blocks/documents/"
                    + urllib.parse.quote(str(request_row["remote_document_id"]), safe="")
                    + f"/blocks/{int(block['index'])}"
                )
                endpoint = f"{peer['base_url']}{path}?{query}"
                source_token = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
                if not source_token:
                    raise ValueError("Lokaler Federation-Token fehlt für peer-signiertes V2-Dedup")
                headers = peer_auth_headers(
                    local_peer_id(),
                    source_token,
                    "GET",
                    path,
                    b"",
                )
                with _request(endpoint, token=token, headers=headers, timeout=120) as response:
                    data = response.read(length + 1)
                digest = sha512_bytes(data)
                actual = scoped_block_token(token, session, digest)
                if len(data) != length or not hmac.compare_digest(actual, proof):
                    raise ValueError("Remote V2-Dedup-Block ist ungültig")
                block_store.put_cached_block(digest, data)
                local[proof] = digest
                network_blocks += 1
                network_bytes += length
            target.seek(int(block["offset"]))
            target.write(data)

    if not verify_file(partial, request_row["blob_hash"]):
        partial.unlink(missing_ok=True)
        raise ValueError("SHA-256-Endprüfung der V2-Dedup-Rekonstruktion fehlgeschlagen")

    federation.record_event(
        "scoped_content_blocks_reused",
        transfer_id=request_row["request_id"],
        peer_id=request_row["peer_id"],
        detail={
            "local_blocks": local_blocks,
            "network_blocks": network_blocks,
            "local_bytes": local_bytes,
            "network_bytes": network_bytes,
            "total_bytes": int(manifest["size"]),
            "indexed": index_result,
            "protocol": "v2-scoped-dedup",
        },
    )
    return partial
