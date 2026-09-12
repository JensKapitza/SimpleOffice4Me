"""Validation helpers for federation trust."""
from .federation_trust_constants import DIRECT_ONLY, RECOMMENDATION_ONLY, TRANSITIVE

TRUST_LEVELS = {"NONE", "LOW", "NORMAL", "HIGH"}
VERIFY = {"KNOWN_UNVERIFIED", "VERIFIED_ONE_WAY", "VERIFIED_MUTUAL", "VERIFIED_IN_PERSON", "VERIFIED_ADMIN"}
PROPAGATION = {DIRECT_ONLY, RECOMMENDATION_ONLY, TRANSITIVE}


def choice(value, allowed, default):
    value = str(value or default).upper()
    if value not in allowed:
        raise ValueError("invalid federation trust value")
    return value


def propagation(value, max_hops=0):
    value = choice(value, PROPAGATION, DIRECT_ONLY)
    return value, max(0, min(int(max_hops), 2)) if value == TRANSITIVE else 0
