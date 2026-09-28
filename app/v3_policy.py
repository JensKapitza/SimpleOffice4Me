"""Fail-closed authorization facade that mirrors existing SimpleOffice rules."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .v3_capabilities import enabled as capability_enabled


ACTIONS=frozenset({"read","create","update","delete","link","share","export","execute"})


@dataclass(frozen=True)
class Resource:
    type: str
    id: str=""


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    resource_type: str
    action: str


ResourceChecker=Callable[[str,str,Resource],bool]
FeatureChecker=Callable[[str,str],bool]


class PolicyFacade:
    def __init__(self, feature_checker: FeatureChecker):
        self.feature_checker=feature_checker
        self._resources: dict[str,tuple[str,ResourceChecker]]={}

    def register(self, resource_type: str, *, feature: str="", checker: ResourceChecker) -> None:
        resource_type=str(resource_type).strip()
        if not resource_type or resource_type in self._resources:
            raise ValueError("resource type must be unique and non-empty")
        self._resources[resource_type]=(str(feature).strip(),checker)

    def decide(self, principal: str, action: str, resource: Resource) -> PolicyDecision:
        principal=str(principal).strip(); action=str(action).strip()
        if not principal:
            return PolicyDecision(False,"missing_principal",resource.type,action)
        if action not in ACTIONS:
            return PolicyDecision(False,"unknown_action",resource.type,action)
        registered=self._resources.get(resource.type)
        if registered is None:
            return PolicyDecision(False,"unknown_resource",resource.type,action)
        feature,checker=registered
        try:
            if feature and not self.feature_checker(principal,feature):
                return PolicyDecision(False,"feature_denied",resource.type,action)
            allowed=bool(checker(principal,action,resource))
        except Exception:
            return PolicyDecision(False,"policy_error",resource.type,action)
        return PolicyDecision(allowed,"allowed" if allowed else "resource_denied",resource.type,action)

    def can(self, principal: str, action: str, resource: Resource) -> bool:
        return self.decide(principal,action,resource).allowed

    def filter_visible(self, principal: str, resources: Iterable[Resource], *, limit: int=500) -> list[Resource]:
        result=[]
        for resource in resources:
            if len(result)>=max(1,min(500,int(limit))):
                break
            if self.can(principal,"read",resource):
                result.append(resource)
        return result


def flask_feature_checker(principal: str, feature: str) -> bool:
    """Adapter to the existing user/feature permission model."""
    from .access_control import has_feature
    from .db import get_db
    row=get_db().execute(
        "SELECT id,username,is_admin,is_disabled FROM user WHERE username=?",
        (str(principal),),
    ).fetchone()
    return bool(row and has_feature(row,feature))


def contact_checker(root: str | Path) -> ResourceChecker:
    """Mirror ContactStore visibility/management; do not invent new contact ACLs."""
    root=Path(root).expanduser().resolve()
    def check(principal: str, action: str, resource: Resource) -> bool:
        from .contact_store import ContactStore
        store=ContactStore(root)
        if action=="create":
            return True
        if not resource.id:
            return False
        if action in {"read","export"}:
            try:
                store.get(resource.id,principal)
                return True
            except ValueError:
                return False
        if action in {"update","delete","link","share"}:
            return store.can_manage(resource.id,principal)
        return False
    return check


def default_policy(root: str | Path) -> PolicyFacade:
    facade=PolicyFacade(flask_feature_checker)
    facade.register("contact",feature="contacts",checker=contact_checker(root))
    return facade


def can_with_legacy(
    facade: PolicyFacade,
    principal: str,
    action: str,
    resource: Resource,
    legacy_check: Callable[[],bool],
    *,
    environ: Mapping[str,str] | None=None,
) -> bool:
    """Cutover helper: old authorization remains authoritative until v3.policy is enabled."""
    if not capability_enabled("v3.policy",environ):
        return bool(legacy_check())
    return facade.can(principal,action,resource)
