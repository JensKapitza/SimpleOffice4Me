"""Safe import of booked FinTS transactions into the immutable finance store."""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from .finance_fints import (
    FinTSAuthenticationRequired,
    default_fetch_window,
    fetch_transactions,
    fingerprint_for_account,
)
from .finance_store import FinanceStore


def _summary(tx: dict[str, Any]) -> dict[str, Any]:
    """Only display normalized non-secret transaction fields in the sync report."""
    return {key: tx.get(key, "") for key in (
        "booking_date", "value_date", "amount_cents", "currency",
        "counterparty_name", "purpose", "bank_transaction_id"
    )}


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
        challenge_state: dict[str, Any] | None = None,
        tan: str = "",
    ) -> dict[str, Any]:
        connection = self.store.bank_connection(connection_id, actor)
        account_id = str(mapping["account_id"])
        if challenge_state is None:
            latest = self.store.latest_bank_booking_date(account_id, actor)
            start, end = default_fetch_window(latest, today=today)
        else:
            if (
                challenge_state.get("operation") != "transactions"
                or challenge_state.get("remote_account_id") != str(mapping["remote_account_id"])
                or challenge_state.get("local_account_id") != account_id
            ):
                raise ValueError("FinTS-SCA-Sitzung passt nicht zu dieser Kontozuordnung")
            try:
                start = date.fromisoformat(str(challenge_state["start_date"]))
                end = date.fromisoformat(str(challenge_state["end_date"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("FinTS-SCA-Sitzung enthält einen ungültigen Abrufzeitraum") from exc
        fetched = fetch_transactions(
            connection,
            str(mapping["remote_account_id"]),
            pin,
            start_date=start,
            end_date=end,
            challenge_state=challenge_state,
            tan=tan,
        )

        # Stable bank IDs remain authoritative. For transactions without one,
        # occurrence counting avoids reimporting an overlap while preserving two
        # genuinely identical payments returned by the bank in the same window.
        existing_counts = self.store.fingerprint_counts(
            account_id, actor, start_date=start.isoformat(), end_date=end.isoformat()
        )
        seen = Counter()
        created = existing = possible = 0
        fetched_rows = []
        for tx in fetched:
            fingerprint = fingerprint_for_account(account_id, tx)
            seen[fingerprint] += 1
            if not tx.get("bank_transaction_id") and seen[fingerprint] <= existing_counts.get(fingerprint, 0):
                existing += 1
                fetched_rows.append({**_summary(tx), "state": "already_imported"})
                continue
            values = dict(tx)
            values["account_id"] = account_id
            row, state = self.store.import_bank_transaction(values, actor)
            fetched_rows.append({**_summary(tx), "state": state})
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
            "transactions": fetched_rows,
            "coverage": "not_verified",
        }

    def sync_connection(
        self,
        connection_id: str,
        pin: str,
        actor: str,
        *,
        today: date | None = None,
        challenge_state: dict[str, Any] | None = None,
        tan: str = "",
    ) -> dict[str, Any]:
        connection = self.store.bank_connection(connection_id, actor)
        if str(connection.get("status", "")).casefold() == "disabled":
            raise ValueError("Bankverbindung ist deaktiviert")
        mappings = self.store.bank_account_mappings(connection_id, actor)
        if not mappings:
            raise ValueError("Für diese Bankverbindung ist noch kein Konto zugeordnet")
        completed_mappings = list((challenge_state or {}).get("completed_mappings", []))
        completed_ids = {item["remote_account_id"] for item in completed_mappings}
        challenged_remote_id = str((challenge_state or {}).get("remote_account_id", ""))
        current_mappings = {str(item["remote_account_id"]): str(item["account_id"]) for item in mappings}
        if any(current_mappings.get(item["remote_account_id"]) != item["account_id"] for item in completed_mappings):
            raise ValueError("Eine bereits synchronisierte Kontozuordnung wurde geändert; bitte den Abruf neu starten")
        if challenge_state is not None and not any(
            item["remote_account_id"] == challenged_remote_id
            and item["account_id"] == challenge_state.get("local_account_id")
            for item in mappings
        ):
            raise ValueError("FinTS-SCA-Kontozuordnung ist nicht mehr verfügbar")
        self.store.update_bank_connection_status(connection_id, actor, "syncing")
        results = []
        try:
            for mapping in mappings:
                remote_id = str(mapping["remote_account_id"])
                if remote_id in completed_ids:
                    continue
                state_for_mapping = challenge_state if remote_id == challenged_remote_id else None
                try:
                    result = self.sync_mapping(
                        connection_id, mapping, pin, actor, today=today,
                        challenge_state=state_for_mapping, tan=tan if state_for_mapping else "",
                    )
                except FinTSAuthenticationRequired as exc:
                    if exc.challenge_state:
                        exc.challenge_state["local_account_id"] = str(mapping["account_id"])
                        exc.challenge_state["completed_mappings"] = [
                            *completed_mappings,
                            *[
                                {"remote_account_id": str(item["remote_account_id"]),
                                 "account_id": str(item["account_id"])}
                                for item in results
                            ],
                        ]
                    raise
                results.append(result)
        except Exception as exc:
            # Error text is bounded by the store and must never include the PIN.
            self.store.update_bank_connection_status(connection_id, actor, "error", error=str(exc))
            raise
        self.store.update_bank_connection_status(connection_id, actor, "connected", successful=True)
        completed_names = [
            str(item.get("name", item["account_id"]))
            for item in mappings
            if str(item["remote_account_id"]) in completed_ids
        ]
        return {
            "connection_id": connection_id,
            "accounts": results,
            "previously_synced_accounts": completed_names,
            "fetched": sum(item["fetched"] for item in results),
            "created": sum(item["created"] for item in results),
            "existing": sum(item["existing"] for item in results),
            "possible_duplicates": sum(item["possible_duplicates"] for item in results),
            "coverage": "not_verified",
        }
