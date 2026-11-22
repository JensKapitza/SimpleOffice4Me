"""XRechnung UBL 2.1 export generated from the frozen invoice snapshot.

The generator never recomputes commercial content silently: it replays the
stored snapshot strings and re-validates the sums with Decimal, raising on
any inconsistency instead of correcting it. Missing optional data is
omitted (KoSIT then reports precisely what is missing); nothing is
invented.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from typing import Any

from tools.install_xrechnung_validator import XRECHNUNG_SPEC_ID

NS = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
INVOICE_TYPE_CODE = "380"

for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)


def _q(tag: str) -> str:
    if ":" in tag:
        prefix, _, name = tag.partition(":")
        return f"{{{NS[prefix]}}}{name}"
    return f"{{{NS['']}}}{tag}"


def _text(parent: ET.Element, tag: str, value: Any, attrs: dict[str, str] | None = None) -> None:
    element = ET.SubElement(parent, _q(tag), attrib=attrs or {})
    element.text = str(value)


def _money(value: Any, what: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"snapshot has invalid amount for {what}") from exc


def validate_snapshot_sums(snapshot: dict[str, Any]) -> None:
    """Recompute snapshot sums; raise on any inconsistency (never fix)."""
    if not isinstance(snapshot, dict):
        raise ValueError("invoice snapshot is invalid")
    lines = snapshot.get("lines", [])
    if not isinstance(lines, list) or not lines:
        raise ValueError("invoice snapshot has no lines")
    totals = snapshot.get("totals", {})
    if not isinstance(totals, dict):
        raise ValueError("invoice snapshot has no totals")
    net = Decimal("0")
    tax = Decimal("0")
    for line in lines:
        if not isinstance(line, dict):
            raise ValueError("invoice snapshot has an invalid line")
        net += _money(line.get("net_total"), "line net total")
        tax += _money(line.get("tax_total"), "line tax total")
    gross = net + tax
    for key, expected in (("net", net), ("tax", tax), ("gross", gross)):
        if _money(totals.get(key), f"total {key}") != expected:
            raise ValueError(f"snapshot totals are inconsistent ({key})")
    groups: dict[str, list[Decimal]] = {}
    for line in lines:
        rate = str(line.get("vat_rate", ""))
        bucket = groups.setdefault(rate, [Decimal("0"), Decimal("0")])
        bucket[0] += _money(line.get("net_total"), "line net total")
        bucket[1] += _money(line.get("tax_total"), "line tax total")
    stored_groups = totals.get("vat_groups", {})
    if not isinstance(stored_groups, dict):
        raise ValueError("invoice snapshot has no VAT groups")
    for rate, (basis, amount) in groups.items():
        stored = stored_groups.get(rate, {})
        if not isinstance(stored, dict):
            raise ValueError(f"snapshot VAT group {rate} is invalid")
        if _money(stored.get("basis"), "VAT basis") != basis or _money(stored.get("tax"), "VAT tax") != amount:
            raise ValueError(f"snapshot VAT group {rate} is inconsistent")


def _category(line: dict[str, Any]) -> str:
    explicit = str(line.get("vat_category", "") or "")
    if explicit:
        return explicit
    return "S" if _money(line.get("vat_rate", "0"), "VAT rate") > 0 else "Z"


def _party(parent: ET.Element, tag: str, party: dict[str, Any], currency: str) -> None:
    del currency
    holder = ET.SubElement(parent, _q(tag))
    node = ET.SubElement(holder, _q("cac:Party"))
    endpoint = party.get("electronic_address", {})
    if isinstance(endpoint, dict) and endpoint.get("scheme") and endpoint.get("id"):
        _text(node, "cbc:EndpointID", endpoint["id"], {"schemeID": str(endpoint["scheme"])})
    _text(node, "cac:PartyName", "")
    ET.SubElement(node.find(_q("cac:PartyName")), _q("cbc:Name")).text = str(party.get("name", ""))
    address = ET.SubElement(node, _q("cac:PostalAddress"))
    if party.get("street"):
        _text(address, "cbc:StreetName", party["street"])
    if party.get("city"):
        _text(address, "cbc:CityName", party["city"])
    if party.get("postal"):
        _text(address, "cbc:PostalZone", party["postal"])
    country = ET.SubElement(address, _q("cac:Country"))
    _text(country, "cbc:IdentificationCode", party.get("country", ""))
    vat_id = str(party.get("vat_id", "") or "")
    if vat_id:
        scheme = ET.SubElement(node, _q("cac:PartyTaxScheme"))
        _text(scheme, "cbc:CompanyID", vat_id)
        tax = ET.SubElement(scheme, _q("cac:TaxScheme"))
        _text(tax, "cbc:ID", "VAT")
    legal = ET.SubElement(node, _q("cac:PartyLegalEntity"))
    _text(legal, "cbc:RegistrationName", party.get("name", ""))
    contact = party.get("contact", {})
    if isinstance(contact, dict) and contact.get("name") and contact.get("phone") and contact.get("email"):
        point = ET.SubElement(node, _q("cac:Contact"))
        _text(point, "cbc:Name", contact["name"])
        _text(point, "cbc:Telephone", contact["phone"])
        _text(point, "cbc:ElectronicMail", contact["email"])


def build_ubl_invoice(snapshot: dict[str, Any]) -> bytes:
    """Build a standard-conformant UBL 2.1 Invoice from a snapshot."""
    validate_snapshot_sums(snapshot)
    currency = str(snapshot.get("currency", "") or "")
    if not currency:
        raise ValueError("invoice snapshot has no currency")
    number = str(snapshot.get("invoice_number", "") or "")
    if not number:
        raise ValueError("invoice snapshot has no invoice number")
    if not snapshot.get("issue_date"):
        raise ValueError("invoice snapshot has no issue date")

    root = ET.Element(_q("Invoice"))
    _text(root, "cbc:CustomizationID", XRECHNUNG_SPEC_ID)
    _text(root, "cbc:ProfileID", PROFILE_ID)
    _text(root, "cbc:ID", number)
    _text(root, "cbc:IssueDate", snapshot["issue_date"])
    if snapshot.get("due_date"):
        _text(root, "cbc:DueDate", snapshot["due_date"])
    _text(root, "cbc:InvoiceTypeCode", INVOICE_TYPE_CODE)
    _text(root, "cbc:DocumentCurrencyCode", currency)
    buyer_reference = str(snapshot.get("buyer_reference", "") or "")
    if buyer_reference:
        _text(root, "cbc:BuyerReference", buyer_reference)
    if snapshot.get("order_reference"):
        order = ET.SubElement(root, _q("cac:OrderReference"))
        _text(order, "cbc:ID", snapshot["order_reference"])
    if snapshot.get("contract_reference"):
        contract = ET.SubElement(root, _q("cac:ContractDocumentReference"))
        _text(contract, "cbc:ID", snapshot["contract_reference"])

    seller = snapshot.get("seller", {}) if isinstance(snapshot.get("seller"), dict) else {}
    buyer = snapshot.get("buyer", {}) if isinstance(snapshot.get("buyer"), dict) else {}
    _party(root, "cac:AccountingSupplierParty", seller, currency)
    _party(root, "cac:AccountingCustomerParty", buyer, currency)

    period = snapshot.get("service_period", {})
    if isinstance(period, dict) and period.get("from") and period.get("to"):
        window = ET.SubElement(root, _q("cac:InvoicePeriod"))
        _text(window, "cbc:StartDate", period["from"])
        _text(window, "cbc:EndDate", period["to"])
    elif snapshot.get("service_date"):
        delivery = ET.SubElement(root, _q("cac:Delivery"))
        _text(delivery, "cbc:ActualDeliveryDate", snapshot["service_date"])

    if seller.get("iban"):
        means = ET.SubElement(root, _q("cac:PaymentMeans"))
        _text(means, "cbc:PaymentMeansCode", "58")
        account = ET.SubElement(means, _q("cac:PayeeFinancialAccount"))
        _text(account, "cbc:ID", seller["iban"])
        if seller.get("bic"):
            branch = ET.SubElement(account, _q("cac:FinancialInstitutionBranch"))
            _text(branch, "cbc:ID", seller["bic"])
    if snapshot.get("payment_terms"):
        terms = ET.SubElement(root, _q("cac:PaymentTerms"))
        _text(terms, "cbc:Note", snapshot["payment_terms"])

    totals = snapshot["totals"]
    tax_total = ET.SubElement(root, _q("cac:TaxTotal"))
    _text(tax_total, "cbc:TaxAmount", totals["tax"], {"currencyID": currency})
    for rate in sorted(totals.get("vat_groups", {}), key=str):
        amounts = totals["vat_groups"][rate]
        group_lines = [line for line in snapshot["lines"]
                       if isinstance(line, dict) and str(line.get("vat_rate", "")) == str(rate)]
        categories = {_category(line) for line in group_lines}
        code = next(iter(categories)) if len(categories) == 1 else (
            "S" if Decimal(str(rate)) > 0 else "Z")
        reason = next((str(line.get("exemption_reason", "") or "") for line in group_lines
                       if line.get("exemption_reason")), "")
        subtotal = ET.SubElement(tax_total, _q("cac:TaxSubtotal"))
        _text(subtotal, "cbc:TaxableAmount", amounts["basis"], {"currencyID": currency})
        _text(subtotal, "cbc:TaxAmount", amounts["tax"], {"currencyID": currency})
        category = ET.SubElement(subtotal, _q("cac:TaxCategory"))
        _text(category, "cbc:ID", code)
        _text(category, "cbc:Percent", rate)
        if reason:
            _text(category, "cbc:TaxExemptionReason", reason)
        scheme = ET.SubElement(category, _q("cac:TaxScheme"))
        _text(scheme, "cbc:ID", "VAT")

    monetary = ET.SubElement(root, _q("cac:LegalMonetaryTotal"))
    _text(monetary, "cbc:LineExtensionAmount", totals["net"], {"currencyID": currency})
    _text(monetary, "cbc:TaxExclusiveAmount", totals["net"], {"currencyID": currency})
    _text(monetary, "cbc:TaxInclusiveAmount", totals["gross"], {"currencyID": currency})
    _text(monetary, "cbc:PayableAmount", totals["due"] if totals.get("due") else totals["gross"],
          {"currencyID": currency})

    for line in snapshot["lines"]:
        item = ET.SubElement(root, _q("cac:InvoiceLine"))
        _text(item, "cbc:ID", line.get("line_id", ""))
        _text(item, "cbc:InvoicedQuantity", line.get("quantity", ""),
              {"unitCode": str(line.get("unit", "") or "C62")})
        _text(item, "cbc:LineExtensionAmount", line.get("net_total", ""), {"currencyID": currency})
        discount = _money(line.get("discount_percent", "0") or "0", "discount percent")
        if discount > 0:
            # UBL sequence: AllowanceCharge precedes Item within InvoiceLine.
            gross_line = (_money(line.get("net_unit_price", "0"), "net unit price")
                          * _money(line.get("quantity", "0"), "quantity"))
            allowance = ET.SubElement(item, _q("cac:AllowanceCharge"))
            _text(allowance, "cbc:ChargeIndicator", "false")
            _text(allowance, "cbc:AllowanceChargeReasonCode", "95")
            _text(allowance, "cbc:AllowanceChargeReason",
                  f"Rabatt {line.get('discount_percent', '')} %")
            _text(allowance, "cbc:MultiplierFactorNumeric", line.get("discount_percent", ""))
            _text(allowance, "cbc:Amount",
                  f"{(gross_line - _money(line.get('net_total', '0'), 'net total')):.2f}",
                  {"currencyID": currency})
            _text(allowance, "cbc:BaseAmount", f"{gross_line:.2f}", {"currencyID": currency})
        product = ET.SubElement(item, _q("cac:Item"))
        _text(product, "cbc:Description", line.get("description", ""))
        _text(product, "cbc:Name", line.get("object_name", "") or line.get("description", ""))
        classified = ET.SubElement(product, _q("cac:ClassifiedTaxCategory"))
        _text(classified, "cbc:ID", _category(line))
        _text(classified, "cbc:Percent", line.get("vat_rate", ""))
        # NOTE: no TaxExemptionReason here (UBL-CR-601); reasons live on the
        # TaxSubtotal level where KoSIT expects them.
        scheme = ET.SubElement(classified, _q("cac:TaxScheme"))
        _text(scheme, "cbc:ID", "VAT")
        price = ET.SubElement(item, _q("cac:Price"))
        _text(price, "cbc:PriceAmount", line.get("net_unit_price", ""), {"currencyID": currency})
        _text(price, "cbc:BaseQuantity", "1", {"unitCode": str(line.get("unit", "") or "C62")})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
