"""Non-destructive matching proposals for bank transactions.

The matcher never books, deletes or mutates source objects.  It produces ranked
proposals which a domain adapter/UI can confirm later.  This keeps imported bank
raw data independent from invoices, receipts, contracts and rentals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from .finance_schema import normalize_iban, text

_REFERENCE_RE = re.compile(r"[A-Z0-9][A-Z0-9._/-]{2,}", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    source_type: str
    source_id: str
    amount_cents: int
    due_date: str = ""
    counterparty_name: str = ""
    counterparty_iban: str = ""
    reference: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class MatchProposal:
    source_type: str
    source_id: str
    score: int
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"source_type": self.source_type, "source_id": self.source_id, "score": self.score, "reasons": list(self.reasons)}


def _tokens(value: Any) -> set[str]:
    return {token.casefold() for token in re.findall(r"[\w-]{3,}", text(value, 2000), re.UNICODE)}


def _iban(value: Any) -> str:
    try:
        return normalize_iban(value)
    except ValueError:
        return ""


def _date_distance(left: str, right: str) -> int | None:
    if not left or not right:
        return None
    try:
        return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)
    except ValueError:
        return None


def propose_matches(transaction: dict[str, Any], candidates: Iterable[MatchCandidate], *, limit: int = 8) -> list[MatchProposal]:
    """Return explainable proposals; no score is an automatic booking decision."""
    tx_amount = int(transaction.get("amount_cents", 0))
    tx_date = str(transaction.get("booking_date", ""))
    tx_iban = _iban(transaction.get("counterparty_iban"))
    tx_text = " ".join((text(transaction.get("counterparty_name"), 300), text(transaction.get("purpose"), 2000)))
    tx_tokens = _tokens(tx_text)
    tx_folded = tx_text.casefold()
    proposals: list[MatchProposal] = []
    for candidate in candidates:
        if not candidate.source_id or not candidate.source_type:
            continue
        score = 0
        reasons: list[str] = []
        if abs(tx_amount) == abs(int(candidate.amount_cents)):
            score += 50
            reasons.append("Betrag stimmt exakt überein")
        else:
            # Different amounts remain eligible for later partial-/combined-payment handling,
            # but must not receive an amount-match score.
            difference = abs(abs(tx_amount) - abs(int(candidate.amount_cents)))
            if difference <= 1:
                score += 45
                reasons.append("Betrag weicht nur um einen Cent ab")
        candidate_iban = _iban(candidate.counterparty_iban)
        if tx_iban and candidate_iban and tx_iban == candidate_iban:
            score += 20
            reasons.append("IBAN stimmt überein")
        reference = text(candidate.reference, 300)
        if reference and len(reference) >= 3 and reference.casefold() in tx_folded:
            score += 20
            reasons.append("Referenz steht im Verwendungszweck")
        distance = _date_distance(tx_date, candidate.due_date)
        if distance is not None:
            if distance <= 3:
                score += 10
                reasons.append("Datum liegt höchstens drei Tage auseinander")
            elif distance <= 14:
                score += 5
                reasons.append("Datum liegt höchstens 14 Tage auseinander")
        candidate_tokens = _tokens(f"{candidate.counterparty_name} {candidate.description}")
        overlap = tx_tokens & candidate_tokens
        if overlap:
            ratio = len(overlap) / max(1, min(len(tx_tokens), len(candidate_tokens)))
            if ratio >= 0.5:
                score += 10
                reasons.append("Name/Beschreibung passt")
            elif ratio >= 0.25:
                score += 5
                reasons.append("Name/Beschreibung teilweise passend")
        if score:
            proposals.append(MatchProposal(candidate.source_type, candidate.source_id, min(score, 100), tuple(reasons)))
    proposals.sort(key=lambda item: (-item.score, item.source_type, item.source_id))
    return proposals[: max(1, min(int(limit), 50))]


def reference_tokens(value: Any) -> tuple[str, ...]:
    """Extract normalized reference-like tokens for adapters and diagnostics."""
    seen: list[str] = []
    for match in _REFERENCE_RE.finditer(text(value, 2000)):
        token = match.group(0).upper()
        if token not in seen:
            seen.append(token)
    return tuple(seen[:100])
