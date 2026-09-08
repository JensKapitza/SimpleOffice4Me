"""Web entry point for local data-quality roulette.

The first UI intentionally accepts no arbitrary object ids or remote URLs. It
renders only challenges already filtered by the policy/ACL engine. Real data
adapters can add candidates without weakening this boundary.
"""
from __future__ import annotations

from flask import Blueprint, g, render_template, request

from .auth import login_required
from .gamification_engine import Candidate, roulette
from .gamification_policy import GamePolicy

bp = Blueprint("gamification", __name__, url_prefix="/gamification")


def _policy() -> GamePolicy:
    # Conservative first rollout: local-only, no originals, proposals only.
    return GamePolicy(
        scope="local",
        providers=frozenset({"images", "documents", "contacts"}),
        preview_allowed=True,
        original_allowed=False,
        submit_proposals=True,
        auto_accept_consensus=False,
    )


def _local_candidates(actor: str) -> list[Candidate]:
    """Return ACL-checked candidates.

    Kept empty until the existing stores are connected through dedicated
    adapters. This fail-closed behavior is deliberate: merely enabling the
    blueprint must never expose repository data.
    """
    return []


@bp.get("/")
@login_required
def index():
    actor = str(g.user["username"])
    challenge = roulette(_policy(), actor, _local_candidates(actor))
    return render_template("gamification/index.html", challenge=challenge)


@bp.post("/answer")
@login_required
def answer():
    # Answers are not accepted until challenge ids are persisted and bound to
    # the authenticated session. Never trust provider/object ids from a form.
    return render_template(
        "gamification/index.html",
        challenge=None,
        message="Antwortspeicherung wird nach der sicheren Session-Bindung aktiviert.",
    ), 409
