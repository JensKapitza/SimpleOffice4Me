"""Challenge selection without bypassing the normal application ACL."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .gamification_policy import GamePolicy
from .gamification_providers import Challenge, get_provider


@dataclass(frozen=True)
class Candidate:
    provider: str
    object_ref: str
    data: Mapping[str, Any]
    normal_read_allowed: bool
    resource_class: str = ""
    collection: str = ""
    federation_allowed: bool = True
    organization_member: bool = False


def eligible_challenges(policy: GamePolicy, actor: str, candidates: Iterable[Candidate]) -> list[Challenge]:
    challenges: list[Challenge] = []
    for candidate in candidates:
        provider = get_provider(candidate.provider)
        if not provider.can_expose(
            policy, actor=actor, object_ref=candidate.object_ref,
            normal_read_allowed=candidate.normal_read_allowed,
            resource_class=candidate.resource_class,
            collection=candidate.collection,
            federation_allowed=candidate.federation_allowed,
            organization_member=candidate.organization_member,
        ):
            continue
        data = dict(candidate.data)
        if candidate.provider == "contacts":
            data["policy"] = policy
        challenges.extend(provider.build_challenges(candidate.object_ref, data))
    return challenges


def roulette(policy: GamePolicy, actor: str, candidates: Iterable[Candidate], *, seed: int | None = None) -> Challenge | None:
    choices = eligible_challenges(policy, actor, candidates)
    if not choices:
        return None
    rng = random.Random(seed)
    return rng.choice(choices)
