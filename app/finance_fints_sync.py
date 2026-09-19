"""Safe import of booked FinTS transactions into the immutable finance store."""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from .finance_fints import default_fetch_window, fetch_transactions, fingerprint_for_account
from .finance_store import FinanceStore


class FinTSSyncService:
    def __init__(self, store: FinanceStore):
        self.store = store

    def sync_mapping(
        self,
        connection_id: str,
        mapping: dict[str, Any],
        pin: str,
        actor: str,
        *,
        today: date | None = None,
    ) -> dict[str, Any]:
        connection = self.store.bank_connection(connection_id, actor)
        account_id = str(mapping["account_id"])
        latest = self.store.latest_bank_booking_date(account_id, actor)
        start, end = default_fetch_window(latest, today=today)
        fetched = fetch_transactions(
            connection,
            str(mapping["remote_account_id"]),
            pin,
            start_date=start,
            end_date=end,
        )

        # Stable bank IDs remain authoritative. For transactions without one,
        # occurrence counting avoids reimporting an overlap while preserving two
        # genuinely identical payments returned by the bank in the same window.
        existing_counts = self.store.fingerprint_counts(
            account_id, actor, start_date=start.isoformat(), end_date=end.isoformat()
        )
        seen = Counter()
        created = existing = possible = 0
        for tx in fetched:
            fingerprint = fingerprint_for_account(account_id, tx)
            seen[fingerprint] += 1
            if not tx.get("bank_transaction_id") and seen[fingerprint] <= existing_counts.get(fingerprint, 0):
                existing += 1
                continue
            values = dict(tx)
            values["account_id"] = account_id
            row, state = self.store.import_bank_transaction(values, actor)
            if state == "created":
                created += 1
            elif state == "existing":
                existing += 1
            else:
                # A newly inserted row may deliberately remain a duplicate
                # candidate for user review. It is never deleted automatically.
                possible += 1

        return {
            "account_id": account_id,
            "remote_account_id": mapping["remote_account_id"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "fetched": len(fetched),
            "created": created,
            "existing": existing,
            "possible_duplicates": possible,
        }

    def sync_connection(self, connection_id: str, pin: str, actor: str, *, today: date | None = None) -> dict[str, Any]:
        mappings = self.store.bank_account_mappings(connection_id, actor)
        if not mappings:
            raise ValueError("Für diese Bankverbindung ist noch kein Konto zugeordnet")
        self.store.update_bank_connection_status(connection_id, actor, "syncing")
        results = []
        try:
            for mapping in mappings:
                results.append(self.sync_mapping(connection_id, mapping, pin, actor, today=today))
        except Exception as exc:
            # Error text is bounded by the store and must never include the PIN.
            self.store.update_bank_connection_status(connection_id, actor, "error", error=str(exc))
            raise
        self.store.update_bank_connection_status(connection_id, actor, "connected", successful=True)
        return {
            "connection_id": connection_id,
            "accounts": results,
            "fetched": sum(item["fetched"] for item in results),
            "created": sum(item["created"] for item in results),
            "existing": sum(item["existing"] for item in results),
            "possible_duplicates": sum(item["possible_duplicates"] for item in results),
        }
