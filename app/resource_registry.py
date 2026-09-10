"""Resource Commander provider registry."""
from __future__ import annotations

from pathlib import Path

from .federation_store import FederationStore
from .resource_federation import FederationResourceProvider
from .resource_local import LocalResourceProvider
from .resource_mail import MailResourceProvider
from .resource_peer_credentials import peer_commander_token
from .resource_provider import ProviderCapabilities, ProviderError
from .resource_smartview import SmartViewProvider
from .safe_paths import normalize_path


class ResourceRegistry:
    def __init__(self, root: str | Path, master_key: bytes, actor: str):
        self.root = normalize_path(root, strict=True)
        self.master_key = master_key
        self.actor = actor
        self._federation = FederationStore(self.root)

    @staticmethod
    def _document_policy(peer: dict) -> dict:
        policy = peer.get("policy")
        if not isinstance(policy, dict):
            return {}
        resources = policy.get("resources")
        if isinstance(resources, dict) and isinstance(resources.get("documents"), dict):
            return resources["documents"]
        documents = policy.get("documents")
        return documents if isinstance(documents, dict) else {}

    @classmethod
    def _federation_capabilities(cls, peer: dict) -> ProviderCapabilities:
        policy = peer.get("policy") if isinstance(peer.get("policy"), dict) else {}
        documents = cls._document_policy(peer)
        can_read = documents.get("receive") is True
        can_write = documents.get("send") is True
        smart_view = can_read and policy.get("smart_view") is True
        return ProviderCapabilities(
            read=can_read,
            write=can_write,
            delete=False,
            move=False,
            copy=can_read,
            folders=can_read or can_write,
            search=can_read,
            metadata=can_read,
            streaming=can_read,
            smart_view=smart_view,
            server_side_copy=can_write,
        )

    def descriptors(self) -> list[dict]:
        providers = [self.get("self"), self.get("mail")]
        result = [self._descriptor(provider) for provider in providers]
        for peer in self._federation.list_peers():
            if not peer.get("enabled"):
                continue
            result.append({
                "provider_id": f"federation:{peer['peer_id']}",
                "label": peer.get("label") or peer["peer_id"],
                "kind": "federation",
                "capabilities": self._federation_capabilities(peer).to_dict(),
            })
        return result

    def _descriptor(self, provider) -> dict:
        return {
            "provider_id": provider.provider_id,
            "label": provider.label,
            "kind": "self" if provider.provider_id == "self" else provider.provider_id,
            "capabilities": provider.capabilities.to_dict(),
        }

    def get(self, provider_id: str, *, smart: bool = False):
        provider_id = str(provider_id or "self")
        if provider_id == "self":
            return SmartViewProvider(self.root) if smart else LocalResourceProvider(self.root)
        if provider_id == "smart":
            return SmartViewProvider(self.root)
        if provider_id == "mail":
            return MailResourceProvider(self.root, self.master_key, self.actor)
        if provider_id.startswith("federation:"):
            peer_id = provider_id.split(":", 1)[1]
            peer = self._federation.get_peer(peer_id)
            if not peer or not peer.get("enabled"):
                raise ProviderError("Federation-Peer ist nicht verfügbar")
            return FederationResourceProvider(
                peer_id,
                str(peer.get("label") or peer_id),
                str(peer["base_url"]),
                peer_commander_token(peer_id),
                allowed_capabilities=self._federation_capabilities(peer),
            )
        raise ProviderError("Unbekannter Provider")
