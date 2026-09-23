"""Client-side V2 scoped block reuse without stable remote block hashes."""
from __future__ import annotations

import hmac
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

from .federation_blocks import FederationBlockStore, sha512_bytes
from .federation_core import preallocate, verify_file
from .federation_store import FederationStore
from .federation_worker import _json_request, _request
from .v2.scoped_dedup import scoped_block_token, scoped_manifest_valid


def _remote_manifest(peer: dict[str, Any], token: str, blob_hash: str) -> dict[str, Any] | None:
    try:
        manifest = _json_request(
            f"{peer['base_url']}/federation/v2/blocks/blobs/{blob_hash}/manifest",
            token=token,
            timeout=120,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 405, 501, 503}:
            return None
        raise
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
    manifest = _remote_manifest(peer, token, request_row["blob_hash"])
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
                endpoint = (
                    f"{peer['base_url']}/federation/v2/blocks/blobs/"
                    f"{request_row['blob_hash']}/blocks/{int(block['index'])}?{query}"
                )
                with _request(endpoint, token=token, timeout=120) as response:
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
