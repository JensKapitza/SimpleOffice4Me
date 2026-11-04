"""ContextFlow agent demo-analysis routes (proposal only, doubly gated).

Separated from ``web.py`` to keep that file under the 1000-line project
gate. Registered onto the existing ``contextflow`` blueprint via
``register_agent_routes(bp)`` so endpoint names stay
``contextflow.agent_*``. Results are proposals; nothing is applied.
"""

from __future__ import annotations

import time

from flask import request

from ..auth import login_required
from .agent_web import render_agent


def _helpers():
    """Lazy imports from web to avoid a module-load cycle."""
    from .web import _actor, _gate, _now, _root, _vision_settings

    return _actor, _gate, _now, _root, _vision_settings


@login_required
def agent_form():
    """Show the Meta demo-analysis form for one confirmed case."""
    _actor, _gate, _now, _root, _vision_settings = _helpers()
    _gate()
    actor = _actor()
    settings = _vision_settings()
    preselected = str(request.args.get("link_id", "") or "")
    return render_agent(actor, _root(), settings, None, None, 200, preselected)


@login_required
def agent_run():
    """Run the Meta tool loop once; the result stays a proposal."""
    from .meta_agent import (
        MetaAgentError,
        release_agent_run,
        reserve_agent_run,
        run_agent,
    )

    _actor, _gate, _now, _root, _vision_settings = _helpers()
    _gate()
    actor = _actor()
    form = request.form
    settings = _vision_settings()
    submitted = str(form.get("link_id", ""))
    slot, retry_after = reserve_agent_run(actor)
    if slot == "limited":
        body, code = render_agent(
            actor,
            _root(),
            settings,
            None,
            "Zu viele Analysen in kurzer Zeit – bitte später erneut versuchen.",
            429,
            submitted,
        )
        return body, code, {"Retry-After": str(retry_after or 60)}
    if slot == "busy":
        return render_agent(
            actor,
            _root(),
            settings,
            None,
            "Für diesen Benutzer läuft bereits eine Analyse – bitte abwarten.",
            409,
            submitted,
        )
    start = time.monotonic()
    try:
        try:
            result = run_agent(
                actor=actor,
                link_id=submitted,
                consent_meta=form.get("consent_meta") == "yes",
                settings=settings,
                root=_root(),
                now=_now(),
            )
        except PermissionError as exc:
            return render_agent(
                actor,
                _root(),
                settings,
                None,
                str(exc) or "Keine Berechtigung",
                403,
                submitted,
            )
        except ValueError as exc:
            return render_agent(
                actor, _root(), settings, None, str(exc), 400, submitted
            )
        except KeyError:
            return render_agent(
                actor,
                _root(),
                settings,
                None,
                "Analyse gestört, bitte erneut versuchen.",
                500,
                submitted,
            )
        except MetaAgentError as exc:
            return render_agent(
                actor, _root(), settings, None, str(exc), 502, submitted
            )
        run_seconds = time.monotonic() - start
        return render_agent(
            actor, _root(), settings, result, None, 200, submitted, run_seconds
        )
    finally:
        release_agent_run(actor)


def register_agent_routes(bp) -> None:
    """Attach the agent views to the existing blueprint."""
    bp.add_url_rule("/agent", view_func=agent_form, methods=["GET"])
    bp.add_url_rule("/agent/run", view_func=agent_run, methods=["POST"])
