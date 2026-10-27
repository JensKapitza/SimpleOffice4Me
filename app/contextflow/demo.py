"""Synthetic demo seeding for ContextFlow (Batch 3).

Builds one complete follow-up story on real stores under an isolated root:
contact, mail case, completed visit with two open findings, and a confirmed
link whose completion lies five days back. All data is synthetic; never run
this against a production root. Uses only public store APIs (invoice rows
are stored JSON, same documented shape as the readers expect).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..business_document_generation import INVOICE_DIR
from ..calendar_store import CalendarStore
from ..contact_store import ContactStore
from ..document_store import CONTROL_DIR
from ..mail_case_store import MailCaseStore
from ..site_visit_store import SiteVisitStore
from ..todo_store import TodoStore
from .case_links import CaseLinkStore

BERLIN = ZoneInfo("Europe/Berlin")


def seed_demo(root: str | Path, actor: str = "demo") -> dict[str, Any]:
    """Seed the synthetic P0 story; return ids and the completion date."""
    root = Path(root).expanduser()
    completed_on = (datetime.now(BERLIN).date() - timedelta(days=5)).isoformat()

    contact = ContactStore(root).upsert(
        {"display_name": "Musterkunde Demo (synthetisch)", "email": "demo@example.test"},
        actor,
    )
    contact_id = str(contact["contact_id"])
    case_id = MailCaseStore(root).create_case(
        actor,
        "Drucker defekt — Anfahrt und Befund (synthetisch)",
        "demo@example.test",
        "demo-mail-0001",
    )
    visits = SiteVisitStore(root)
    visit = visits.create(
        actor,
        {
            "title": "Ortstermin Drucker (synthetisch)",
            "customer": "Musterkunde Demo (synthetisch)",
            "site": "Werkstatt (synthetisch)",
            "address": "Musterweg 1, 12345 Musterstadt",
            "visit_date": completed_on + "T10:00",
            "notes": "Anfahrt und Befund (synthetisch)",
        },
    )
    visit_id = str(visit["visit_id"])
    visits.add_finding(
        visit_id,
        actor,
        {
            "title": "Papierstau, Ersatzteil offen (synthetisch)",
            "description": "Papierstau im Einzug (synthetisch)",
            "observed_at": completed_on + "T10:00",
        },
    )
    visits.add_finding(
        visit_id,
        actor,
        {
            "title": "Firmware veraltet (synthetisch)",
            "description": "Update steht aus (synthetisch)",
            "observed_at": completed_on + "T11:00",
        },
    )
    visits.update(visit_id, actor, {"status": "completed"})

    rows = [
        {
            "invoice_id": "DEMO-2026-001",
            "contact_id": contact_id,
            "invoice_number": "RE-2026-001",
            "status": "draft",
            "issue_date": completed_on,
            "currency": "EUR",
        }
    ]
    directory = root / CONTROL_DIR / INVOICE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for row in rows:
        path = directory / f"{row['invoice_id']}.json"
        path.write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    CalendarStore(root).add(
        "Rückfrage Kunde (synthetisch)",
        "Kundenrückfrage (synthetisch)",
        completed_on + "T10:00",
        completed_on + "T10:30",
        contact_id,
        actor,
    )
    TodoStore(root).add("Alte Aufgabe (synthetisch)", actor, {"contact_id": contact_id})

    link = CaseLinkStore(root).confirm(
        actor,
        mail_case_id=case_id,
        contact_id=contact_id,
        visit_id=visit_id,
        invoice_id="DEMO-2026-001",
        completed_on=completed_on,
        now=datetime.now(BERLIN),
    )
    return {
        "actor": actor,
        "contact_id": contact_id,
        "case_id": case_id,
        "visit_id": visit_id,
        "link_id": str(link["link_id"]),
        "completed_on": completed_on,
        "invoice_id": "DEMO-2026-001",
        "synthetic": True,
    }
