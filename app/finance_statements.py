"""Bank statement parsing and idempotent import for the shared finance core.

Supported input formats:
- CSV exports with common German/English banking column names
- ISO 20022 CAMT.052/CAMT.053 XML
- MT940 statements

Parsing never mutates finance data. ``FinanceStatementImporter.commit`` is the
only write path and reuses ``FinanceStore`` source/idempotency rules.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from defusedxml import ElementTree as ET

from .finance_schema import currency, normalize_iban, text, transaction_fingerprint
from .finance_store import FinanceStore

_MAX_STATEMENT_BYTES = 25 * 1024 * 1024
_MAX_ROWS = 100_000
_MONEY = Decimal("0.01")


@dataclass(slots=True)
class ParsedStatement:
    format: str
    account_iban: str = ""
    account_reference: str = ""
    currency: str = "EUR"
    period_start: str = ""
    period_end: str = ""
    statement_id: str = ""
    transactions: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "account_iban": self.account_iban,
            "account_reference": self.account_reference,
            "currency": self.currency,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "statement_id": self.statement_id,
            "transactions": [dict(row) for row in self.transactions],
        }


def _checked_bytes(data: bytes | bytearray | memoryview) -> bytes:
    raw = bytes(data)
    if not raw:
        raise ValueError("Kontoauszug ist leer")
    if len(raw) > _MAX_STATEMENT_BYTES:
        raise ValueError("Kontoauszug ist zu groß")
    return raw


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("Zeichensatz des Kontoauszugs wird nicht unterstützt")


def _amount_cents(value: Any) -> int:
    raw = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
    if not raw:
        raise ValueError("Betrag fehlt")
    raw = re.sub(r"[^0-9,.-]", "", raw)
    if raw.count(",") and raw.count("."):
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(raw).quantize(_MONEY, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Betrag ist ungültig") from exc
    cents = int(amount * 100)
    if cents == 0 or abs(cents) > 99_999_999_999:
        raise ValueError("Betrag ist außerhalb des unterstützten Bereichs")
    return cents


def _date_value(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.split("T", 1)[0]
    for pattern in (r"(\d{4})-(\d{2})-(\d{2})", r"(\d{2})\.(\d{2})\.(\d{4})", r"(\d{2})/(\d{2})/(\d{4})"):
        match = re.fullmatch(pattern, raw)
        if not match:
            continue
        parts = match.groups()
        if pattern.startswith(r"(\d{4})"):
            year, month, day = map(int, parts)
        else:
            day, month, year = map(int, parts)
        try:
            return date(year, month, day).isoformat()
        except ValueError as exc:
            raise ValueError(f"Ungültiges Datum: {raw}") from exc
    raise ValueError(f"Nicht unterstütztes Datum: {raw}")


def _header(value: str) -> str:
    value = value.strip().casefold()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "", value)


_CSV_ALIASES: dict[str, tuple[str, ...]] = {
    "booking_date": ("buchungstag", "buchungsdatum", "bookingdate", "date"),
    "value_date": ("valuta", "valutadatum", "wertstellung", "valuedate"),
    "amount": ("betrag", "amount", "umsatz", "transactionamount"),
    "currency": ("waehrung", "currency", "currencycode"),
    "counterparty_name": ("auftraggeberempfaenger", "empfaengerzahlungspflichtiger", "zahlungspflichtiger", "empfaenger", "gegenpartei", "name", "payee", "payer"),
    "counterparty_iban": ("iban", "gegenkontoiban", "empfaengeriban", "auftraggeberiban", "counterpartyiban"),
    "purpose": ("verwendungszweck", "buchungstext", "beschreibung", "purpose", "memo", "description"),
    "bank_transaction_id": ("transaktionsid", "transactionid", "banktransactionid", "umsatzid"),
    "end_to_end_id": ("endtoendreferenz", "endtoendid", "endtoendreference"),
    "mandate_id": ("mandatsreferenz", "mandateid", "mandatereference"),
}


def _csv_column_map(fieldnames: Iterable[str]) -> dict[str, str]:
    normalized = {_header(name): name for name in fieldnames if name is not None}
    result: dict[str, str] = {}
    for target, aliases in _CSV_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                result[target] = normalized[alias]
                break
    if "booking_date" not in result or "amount" not in result:
        raise ValueError("CSV benötigt mindestens Buchungstag und Betrag")
    return result


def _csv_preamble_and_body(text_value: str) -> tuple[list[str], str]:
    lines = text_value.splitlines()
    for index, line in enumerate(lines[:50]):
        if not line.strip():
            continue
        for delimiter in (";", ",", "\t"):
            cells = [_header(cell) for cell in line.split(delimiter)]
            if any(cell in _CSV_ALIASES["booking_date"] for cell in cells) and any(cell in _CSV_ALIASES["amount"] for cell in cells):
                return lines[:index], "\n".join(lines[index:])
    return [], text_value


def parse_csv(raw: bytes) -> list[ParsedStatement]:
    text_value = _decode_text(raw)
    preamble, body = _csv_preamble_and_body(text_value)
    sample = body[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
    except csv.Error:
        class Semi(csv.excel):
            delimiter = ";"
        dialect = Semi
    reader = csv.DictReader(io.StringIO(body), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV enthält keine Kopfzeile")
    columns = _csv_column_map(reader.fieldnames)
    account_iban = ""
    account_reference = ""
    for line in preamble:
        match = re.search(r"\bIBAN\b\s*[:;=]?\s*([A-Z]{2}[0-9A-Z ]{13,34})", line, re.IGNORECASE)
        if match:
            try:
                account_iban = normalize_iban(match.group(1))
            except ValueError:
                pass
        if not account_reference and re.search(r"konto|account", line, re.IGNORECASE):
            account_reference = text(line.split(":", 1)[-1], 100)
    rows: list[dict[str, Any]] = []
    dates: list[str] = []
    detected_currency = "EUR"
    for number, source in enumerate(reader, start=2):
        if len(rows) >= _MAX_ROWS:
            raise ValueError("CSV enthält zu viele Buchungen")
        if not any(str(value or "").strip() for value in source.values()):
            continue
        booking_date = _date_value(source.get(columns["booking_date"], ""))
        value_date = _date_value(source.get(columns.get("value_date", ""), "")) if columns.get("value_date") and source.get(columns["value_date"], "") else ""
        amount = _amount_cents(source.get(columns["amount"], ""))
        row_currency = currency(source.get(columns.get("currency", ""), detected_currency) or detected_currency)
        detected_currency = row_currency
        counterparty_iban = ""
        if columns.get("counterparty_iban"):
            raw_iban = source.get(columns["counterparty_iban"], "")
            try:
                counterparty_iban = normalize_iban(raw_iban)
            except ValueError:
                counterparty_iban = ""
        rows.append({
            "booking_date": booking_date,
            "value_date": value_date,
            "amount_cents": amount,
            "currency": row_currency,
            "counterparty_name": text(source.get(columns.get("counterparty_name", ""), ""), 300),
            "counterparty_iban": counterparty_iban,
            "purpose": text(source.get(columns.get("purpose", ""), ""), 2000),
            "bank_transaction_id": text(source.get(columns.get("bank_transaction_id", ""), ""), 300),
            "end_to_end_id": text(source.get(columns.get("end_to_end_id", ""), ""), 300),
            "mandate_id": text(source.get(columns.get("mandate_id", ""), ""), 300),
            "raw": {str(key): str(value or "") for key, value in source.items() if key is not None},
            "source_row": number,
        })
        dates.append(booking_date)
    if not rows:
        raise ValueError("CSV enthält keine Buchungen")
    return [ParsedStatement(
        format="csv", account_iban=account_iban, account_reference=account_reference,
        currency=detected_currency, period_start=min(dates), period_end=max(dates),
        transactions=rows,
    )]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node.iter() if _local(child.tag) == name]


def _first_text(node: ET.Element | None, *path: str) -> str:
    if node is None:
        return ""
    current: ET.Element | None = node
    for name in path:
        current = next((child for child in list(current) if _local(child.tag) == name), None)
        if current is None:
            return ""
    return text(current.text, 2000)


def _desc_text(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    found = next((child for child in node.iter() if _local(child.tag) == name and text(child.text, 2000)), None)
    return text(found.text, 2000) if found is not None else ""


def _camt_date(node: ET.Element, container: str) -> str:
    parent = next((child for child in list(node) if _local(child.tag) == container), None)
    if parent is None:
        return ""
    value = _desc_text(parent, "Dt") or _desc_text(parent, "DtTm")
    return _date_value(value) if value else ""


def _camt_party(tx: ET.Element | None, credit: bool) -> tuple[str, str]:
    if tx is None:
        return "", ""
    # For incoming credits the counterparty is the debtor, for outgoing debits the creditor.
    party_name = "Dbtr" if credit else "Cdtr"
    account_name = "DbtrAcct" if credit else "CdtrAcct"
    related = next((child for child in tx.iter() if _local(child.tag) == "RltdPties"), None)
    if related is None:
        return "", ""
    party = next((child for child in list(related) if _local(child.tag) == party_name), None)
    account = next((child for child in list(related) if _local(child.tag) == account_name), None)
    name = _desc_text(party, "Nm")
    iban = _desc_text(account, "IBAN")
    try:
        return text(name, 300), normalize_iban(iban)
    except ValueError:
        return text(name, 300), ""


def parse_camt(raw: bytes) -> list[ParsedStatement]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("CAMT XML ist ungültig") from exc
    containers = [node for node in root.iter() if _local(node.tag) in {"Stmt", "Rpt"}]
    if not containers:
        raise ValueError("Kein CAMT.052/053-Kontoauszug gefunden")
    statements: list[ParsedStatement] = []
    for container in containers:
        account = next((child for child in list(container) if _local(child.tag) == "Acct"), None)
        iban = _desc_text(account, "IBAN")
        try:
            iban = normalize_iban(iban)
        except ValueError:
            iban = ""
        account_ref = _desc_text(account, "Othr") or _desc_text(account, "Id")
        statement_id = _first_text(container, "Id")
        rows: list[dict[str, Any]] = []
        dates: list[str] = []
        statement_currency = _desc_text(account, "Ccy") or "EUR"
        for entry in [node for node in list(container) if _local(node.tag) == "Ntry"]:
            amount_node = next((child for child in list(entry) if _local(child.tag) == "Amt"), None)
            if amount_node is None or not text(amount_node.text, 100):
                continue
            cents = _amount_cents(amount_node.text)
            indicator = _first_text(entry, "CdtDbtInd").upper()
            credit = indicator == "CRDT"
            if not credit:
                cents = -abs(cents)
            else:
                cents = abs(cents)
            row_currency = currency(amount_node.attrib.get("Ccy", statement_currency) or statement_currency)
            booking_date = _camt_date(entry, "BookgDt") or _camt_date(entry, "ValDt")
            value_date = _camt_date(entry, "ValDt")
            if not booking_date:
                raise ValueError("CAMT-Buchung ohne Buchungsdatum")
            tx = next((node for node in entry.iter() if _local(node.tag) == "TxDtls"), None)
            counterparty_name, counterparty_iban = _camt_party(tx, credit)
            refs = next((node for node in tx.iter() if _local(node.tag) == "Refs"), None) if tx is not None else None
            remittance = next((node for node in tx.iter() if _local(node.tag) == "RmtInf"), None) if tx is not None else None
            purpose_parts = [text(node.text, 1000) for node in remittance.iter() if _local(node.tag) in {"Ustrd", "AddtlRmtInf"} and text(node.text, 1000)] if remittance is not None else []
            bank_id = _desc_text(refs, "AcctSvcrRef") or _first_text(entry, "AcctSvcrRef") or _first_text(entry, "NtryRef")
            rows.append({
                "booking_date": booking_date,
                "value_date": value_date,
                "amount_cents": cents,
                "currency": row_currency,
                "counterparty_name": counterparty_name,
                "counterparty_iban": counterparty_iban,
                "purpose": text(" ".join(purpose_parts) or _first_text(entry, "AddtlNtryInf"), 2000),
                "bank_transaction_id": text(bank_id, 300),
                "end_to_end_id": text(_desc_text(refs, "EndToEndId"), 300),
                "mandate_id": text(_desc_text(refs, "MndtId"), 300),
                "raw": {"camt_entry_reference": _first_text(entry, "NtryRef"), "credit_debit": indicator},
            })
            dates.append(booking_date)
        if rows:
            statements.append(ParsedStatement(
                format="camt", account_iban=iban, account_reference=text(account_ref, 100),
                currency=currency(statement_currency), period_start=min(dates), period_end=max(dates),
                statement_id=text(statement_id, 200), transactions=rows,
            ))
    if not statements:
        raise ValueError("CAMT-Datei enthält keine Buchungen")
    return statements


def _mt940_date(value: str, year_hint: int | None = None) -> str:
    if re.fullmatch(r"\d{6}", value):
        year = 2000 + int(value[:2]) if int(value[:2]) < 70 else 1900 + int(value[:2])
        return date(year, int(value[2:4]), int(value[4:6])).isoformat()
    if re.fullmatch(r"\d{4}", value) and year_hint:
        return date(year_hint, int(value[:2]), int(value[2:4])).isoformat()
    raise ValueError("Ungültiges MT940-Datum")


def _mt940_purpose(raw: str) -> tuple[str, str, str]:
    fields: dict[str, list[str]] = {}
    current = "20"
    for part in re.split(r"\?(\d{2})", raw):
        if re.fullmatch(r"\d{2}", part):
            current = part
        elif part:
            fields.setdefault(current, []).append(part.strip())
    name = " ".join(fields.get("32", []) + fields.get("33", []))
    iban = ""
    for candidate in re.findall(r"\b[A-Z]{2}[0-9A-Z]{13,32}\b", raw.replace(" ", "").upper()):
        try:
            iban = normalize_iban(candidate)
            break
        except ValueError:
            pass
    purpose = " ".join(fields.get("20", []) + fields.get("21", []) + fields.get("22", []) + fields.get("23", []) + fields.get("24", []) + fields.get("25", []) + fields.get("26", []) + fields.get("27", []) + fields.get("28", []) + fields.get("29", []))
    return text(name, 300), iban, text(purpose or raw, 2000)


def parse_mt940(raw: bytes) -> list[ParsedStatement]:
    value = _decode_text(raw).replace("\r\n", "\n")
    blocks = re.split(r"(?=^:20:)", value, flags=re.MULTILINE)
    statements: list[ParsedStatement] = []
    for block in blocks:
        if ":61:" not in block:
            continue
        account_ref_match = re.search(r"^:25:(.+)$", block, re.MULTILINE)
        statement_match = re.search(r"^:28C?:(.+)$", block, re.MULTILINE)
        account_ref = text(account_ref_match.group(1) if account_ref_match else "", 100)
        iban = ""
        try:
            compact = re.sub(r"[^0-9A-Z]", "", account_ref.upper())
            if compact.startswith(tuple(f"{a}{b}" for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")):
                iban = normalize_iban(compact)
        except ValueError:
            iban = ""
        tags = list(re.finditer(r"^:(\d{2}[A-Z]?):(.*?)(?=^:\d{2}[A-Z]?:|\Z)", block, re.MULTILINE | re.DOTALL))
        rows: list[dict[str, Any]] = []
        dates: list[str] = []
        index = 0
        while index < len(tags):
            tag, payload = tags[index].group(1), tags[index].group(2).strip()
            if tag != "61":
                index += 1
                continue
            match = re.match(r"(?P<date>\d{6})(?P<value>\d{4})?(?P<mark>[R]?[DC])(?P<funds>[A-Z])?(?P<amount>[0-9,]+)(?P<rest>.*)", payload, re.DOTALL)
            if not match:
                raise ValueError("Nicht unterstützte MT940-:61:-Buchung")
            booking_date = _mt940_date(match.group("date"))
            value_date = _mt940_date(match.group("value"), int(booking_date[:4])) if match.group("value") else ""
            cents = abs(_amount_cents(match.group("amount")))
            if match.group("mark").endswith("D"):
                cents = -cents
            rest = text(match.group("rest"), 2000)
            bank_id = ""
            if "//" in rest:
                bank_id = text(rest.split("//", 1)[1].splitlines()[0], 300)
            details = ""
            if index + 1 < len(tags) and tags[index + 1].group(1) == "86":
                details = tags[index + 1].group(2).strip()
                index += 1
            name, counterparty_iban, purpose = _mt940_purpose(details or rest)
            rows.append({
                "booking_date": booking_date,
                "value_date": value_date,
                "amount_cents": cents,
                "currency": "EUR",
                "counterparty_name": name,
                "counterparty_iban": counterparty_iban,
                "purpose": purpose,
                "bank_transaction_id": bank_id,
                "end_to_end_id": "",
                "mandate_id": "",
                "raw": {"mt940_61": payload[:4000], "mt940_86": details[:8000]},
            })
            dates.append(booking_date)
            index += 1
        if rows:
            statements.append(ParsedStatement(
                format="mt940", account_iban=iban, account_reference=account_ref,
                currency="EUR", period_start=min(dates), period_end=max(dates),
                statement_id=text(statement_match.group(1) if statement_match else "", 200),
                transactions=rows,
            ))
    if not statements:
        raise ValueError("MT940-Datei enthält keine Buchungen")
    return statements


def parse_statement_bytes(data: bytes | bytearray | memoryview, filename: str = "") -> list[ParsedStatement]:
    raw = _checked_bytes(data)
    stripped = raw.lstrip()
    suffix = Path(filename or "").suffix.casefold()
    if stripped.startswith(b"<") or suffix in {".xml", ".camt"}:
        return parse_camt(raw)
    text_head = _decode_text(raw[: min(len(raw), 16_384)])
    if re.search(r"^:20:", text_head, re.MULTILINE) and ":61:" in text_head:
        return parse_mt940(raw)
    return parse_csv(raw)


class FinanceStatementImporter:
    """Preview and commit statement imports without modifying source transactions."""

    def __init__(self, store: FinanceStore):
        self.store = store

    def _account(self, actor: str, account_id: str) -> dict[str, Any]:
        candidates = [row for row in self.store.accounts(actor) if row["account_id"] == account_id]
        if not candidates:
            raise ValueError("Konto wurde nicht gefunden")
        return candidates[0]

    def _match_account(self, statement: ParsedStatement, actor: str, account_id: str = "") -> dict[str, Any] | None:
        if account_id:
            account = self._account(actor, account_id)
            if statement.account_iban and account.get("iban") and statement.account_iban != account.get("iban"):
                raise ValueError("IBAN des Kontoauszugs passt nicht zum ausgewählten Konto")
            return account
        if statement.account_iban:
            return self.store.account_by_iban(statement.account_iban, actor)
        return None

    @staticmethod
    def _classify(rows: list[dict[str, Any]], account_id: str, transaction: dict[str, Any]) -> str:
        bank_id = text(transaction.get("bank_transaction_id"), 300)
        if bank_id and any(text(row.get("bank_transaction_id"), 300) == bank_id for row in rows):
            return "existing"
        probe = dict(transaction)
        probe["account_id"] = account_id
        fingerprint = transaction_fingerprint(probe)
        if any(str(row.get("fingerprint", "")) == fingerprint for row in rows):
            return "possible_duplicate"
        end_to_end = text(transaction.get("end_to_end_id"), 300)
        if end_to_end and any(
            text(row.get("end_to_end_id"), 300) == end_to_end
            and int(row.get("amount_cents", 0)) == int(transaction.get("amount_cents", 0))
            and str(row.get("booking_date", "")) == str(transaction.get("booking_date", ""))
            for row in rows
        ):
            return "possible_duplicate"
        mandate = text(transaction.get("mandate_id"), 300)
        if mandate and any(
            text(row.get("mandate_id"), 300) == mandate
            and int(row.get("amount_cents", 0)) == int(transaction.get("amount_cents", 0))
            and str(row.get("booking_date", "")) == str(transaction.get("booking_date", ""))
            for row in rows
        ):
            return "possible_duplicate"
        return "new"

    def preview(self, data: bytes, filename: str, actor: str, *, account_id: str = "") -> dict[str, Any]:
        raw = _checked_bytes(data)
        digest = hashlib.sha256(raw).hexdigest()
        statements = parse_statement_bytes(raw, filename)
        result: list[dict[str, Any]] = []
        totals = {"new": 0, "existing": 0, "possible_duplicate": 0, "unmatched_account": 0}
        for index, statement in enumerate(statements):
            account = self._match_account(statement, actor, account_id)
            existing_rows = self.store.bank_transactions(account["account_id"], actor, limit=5000) if account else []
            transaction_rows: list[dict[str, Any]] = []
            for transaction in statement.transactions:
                state = self._classify(existing_rows, account["account_id"], transaction) if account else "unmatched_account"
                totals[state] += 1
                transaction_rows.append({**transaction, "preview_state": state})
            result.append({
                "statement_index": index,
                "format": statement.format,
                "statement_id": statement.statement_id,
                "account_iban": statement.account_iban,
                "account_reference": statement.account_reference,
                "matched_account": account,
                "period_start": statement.period_start,
                "period_end": statement.period_end,
                "currency": statement.currency,
                "transactions": transaction_rows,
            })
        return {"filename": Path(filename or "kontoauszug").name, "sha256": digest, "statements": result, "summary": totals}

    def commit(self, data: bytes, filename: str, actor: str, *, account_id: str = "", statement_index: int = 0) -> dict[str, Any]:
        raw = _checked_bytes(data)
        digest = hashlib.sha256(raw).hexdigest()
        statements = parse_statement_bytes(raw, filename)
        if not 0 <= int(statement_index) < len(statements):
            raise ValueError("Kontoauszug-Auswahl ist ungültig")
        statement = statements[int(statement_index)]
        account = self._match_account(statement, actor, account_id)
        if account is None:
            raise ValueError("Kontoauszug konnte keinem vorhandenen Konto zugeordnet werden")
        batch, created = self.store.create_import_batch(account["account_id"], {
            "source_type": statement.format,
            "source_name": Path(filename or "kontoauszug").name,
            "file_sha256": digest,
            "period_start": statement.period_start,
            "period_end": statement.period_end,
            "row_count": len(statement.transactions),
        }, actor)
        if not created:
            return {"status": "already_imported", "batch": batch, "created": 0, "existing": len(statement.transactions), "possible_duplicates": 0}
        created_count = existing_count = duplicate_count = 0
        imported: list[dict[str, Any]] = []
        for transaction in statement.transactions:
            values = dict(transaction)
            values["account_id"] = account["account_id"]
            values["import_batch_id"] = batch["batch_id"]
            row, state = self.store.import_bank_transaction(values, actor)
            imported.append(row)
            if state == "created":
                created_count += 1
            elif state == "existing":
                existing_count += 1
            else:
                duplicate_count += 1
        return {
            "status": "imported",
            "batch": batch,
            "account": account,
            "created": created_count,
            "existing": existing_count,
            "possible_duplicates": duplicate_count,
            "transactions": imported,
        }
