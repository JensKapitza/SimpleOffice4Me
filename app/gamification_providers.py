"""Provider API for safe data-quality challenges."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .gamification_policy import GamePolicy, allowed_contact_fields, can_expose


@dataclass(frozen=True)
class Challenge:
    provider: str
    object_ref: str
    kind: str
    prompt: str
    answer_type: str
    payload: dict[str, Any]


AccessCheck = Callable[[str, str], bool]


class Provider:
    name = ""

    def can_expose(self, policy: GamePolicy, *, actor: str, object_ref: str,
                   normal_read_allowed: bool, resource_class: str = "",
                   collection: str = "", field_name: str = "",
                   federation_allowed: bool = True,
                   organization_member: bool = False) -> bool:
        return can_expose(
            policy, provider=self.name, actor=actor,
            normal_read_allowed=normal_read_allowed,
            resource_class=resource_class, collection=collection,
            field_name=field_name, federation_allowed=federation_allowed,
            organization_member=organization_member,
        )

    def build_challenges(self, object_ref: str, data: Mapping[str, Any]) -> list[Challenge]:
        raise NotImplementedError

    def validate_answer(self, challenge: Challenge, answer: Any) -> bool:
        if answer in (None, ""):
            return False
        if challenge.answer_type == "year":
            try:
                year = int(answer)
            except (TypeError, ValueError):
                return False
            return 1800 <= year <= 2200
        if challenge.answer_type == "tags":
            return isinstance(answer, (str, list, tuple))
        return isinstance(answer, str) and 0 < len(answer.strip()) <= 500


class ImageProvider(Provider):
    name = "images"

    def build_challenges(self, object_ref: str, data: Mapping[str, Any]) -> list[Challenge]:
        preview = data.get("preview_url", "")
        base = {"preview_url": preview}
        return [
            Challenge(self.name, object_ref, "year", "Aus welchem Jahr ist dieses Bild?", "year", base.copy()),
            Challenge(self.name, object_ref, "tags", "Was ist auf diesem Bild zu sehen?", "tags", base.copy()),
            Challenge(self.name, object_ref, "place", "Wo koennte dieses Bild aufgenommen worden sein?", "text", base.copy()),
        ]


class DocumentProvider(Provider):
    name = "documents"

    def build_challenges(self, object_ref: str, data: Mapping[str, Any]) -> list[Challenge]:
        safe = {"display_name": str(data.get("display_name", ""))[:200]}
        return [
            Challenge(self.name, object_ref, "document_type", "Was fuer ein Dokument ist das?", "text", safe.copy()),
            Challenge(self.name, object_ref, "tags", "Welche Tags passen zu dieser Datei?", "tags", safe.copy()),
            Challenge(self.name, object_ref, "year", "Aus welchem Jahr stammt die Datei?", "year", safe.copy()),
        ]


class ContactProvider(Provider):
    name = "contacts"

    def build_challenges(self, object_ref: str, data: Mapping[str, Any]) -> list[Challenge]:
        policy = data.get("policy")
        requested = ("street", "house_number", "postal_code", "city", "phone", "mobile", "email", "company")
        fields = allowed_contact_fields(policy, requested) if isinstance(policy, GamePolicy) else ()
        display = str(data.get("display_name", ""))[:200]
        result: list[Challenge] = []
        prompts = {
            "street": "Welche Strasse ist fuer diesen Kontakt aktuell?",
            "house_number": "Welche Hausnummer ist aktuell?",
            "postal_code": "Welche PLZ ist aktuell?",
            "city": "Welcher Ort ist aktuell?",
            "phone": "Welche Telefonnummer ist aktuell?",
            "mobile": "Welche Mobilnummer ist aktuell?",
            "email": "Welche E-Mail-Adresse ist aktuell?",
            "company": "Zu welcher Firma gehoert der Kontakt?",
        }
        for field in fields:
            result.append(Challenge(self.name, object_ref, field, prompts[field], "text", {"display_name": display, "field": field}))
        return result


PROVIDERS: dict[str, Provider] = {
    "images": ImageProvider(),
    "documents": DocumentProvider(),
    "contacts": ContactProvider(),
}


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise ValueError("unknown gamification provider") from exc
