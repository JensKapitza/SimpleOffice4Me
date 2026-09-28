"""Build a fact-separated, versioned field visit report as PDF."""
from __future__ import annotations

import hashlib
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

from .site_visit_store import SiteVisitStore

STATUS_LABELS = {"unreviewed": "Ungeprüft", "confirmed": "Bestätigt", "corrected": "Korrigiert", "not_assessable": "Nicht bewertbar", "rejected": "Verworfen / falsch erhoben"}
EVIDENCE_LABELS = {"observed": "Direkt beobachtet", "measured": "Durch Messung/Log belegt", "derived": "Fachlich abgeleitet", "risk": "Mögliches Risiko", "unknown": "Unbekannt"}


def _p(value: object) -> str:
    return escape(str(value or "")).replace("\n", "<br/>") or "–"


def _table(rows: list[list[object]], widths: list[float] | None = None) -> Table:
    table = Table([[Paragraph(_p(cell), getSampleStyleSheet()["BodyText"]) for cell in row] for row in rows], colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6f4f2")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f5f5b")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), .45, colors.HexColor("#d5dee8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def build_visit_report(record: dict, selected_ids: list[str]) -> tuple[bytes, str, list[str]]:
    """Build one report version; return PDF bytes, SHA-256 and included finding IDs."""
    selected = set(str(value) for value in selected_ids)
    findings = [row for row in record.get("findings", []) if row.get("finding_id") in selected and row.get("status") != "rejected"]
    if len(findings) != len(selected):
        raise ValueError("Auswahl enthält nicht verfügbare oder verworfene Befunde")
    if not findings:
        raise ValueError("Mindestens einen berichtsfähigen Befund auswählen")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M %Z")
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm, title=f"Ortsterminbericht – {record.get('title', '')}", author=str(record.get("owner", "")))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SOHeading", parent=styles["Heading2"], textColor=colors.HexColor("#0f766e"), spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="SOFinding", parent=styles["Heading3"], textColor=colors.HexColor("#1f2937"), spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="SOMuted", parent=styles["BodyText"], textColor=colors.HexColor("#64748b"), fontSize=8.5, leading=11))
    story = [Paragraph("TECHNISCHER ORTSTERMINBERICHT", ParagraphStyle(name="SOBrand", parent=styles["Title"], textColor=colors.HexColor("#0f766e"), alignment=TA_CENTER, fontSize=14)), Spacer(1, 4*mm), Paragraph(_p(record.get("title")), styles["Title"])]
    story.append(_table([["Kunde", "Standort", "Adresse", "Datum"], [record.get("customer", ""), record.get("site", ""), record.get("address", ""), record.get("visit_date", "")]], [34*mm, 43*mm, 73*mm, 25*mm]))
    story.extend([Spacer(1, 4*mm), Paragraph(f"Erstellt {escape(now)} · Version {len(record.get('reports', [])) + 1} · Ersteller { _p(record.get('owner', ''))}", styles["SOMuted"])])
    story.append(Paragraph("Zusammenfassung", styles["SOHeading"]))
    story.append(Paragraph("Dieser Bericht enthält ausgewählte Befunde des Ortstermins. Beobachtungen, Messwerte, fachliche Ableitungen, Risiken und offene Fragen sind nach ihrem Prüf- und Evidenzstatus gekennzeichnet. Eine mögliche Folge wird nicht als nachgewiesenes Ereignis dargestellt.", styles["BodyText"]))
    story.append(Paragraph("Räume und Geräte", styles["SOHeading"]))
    room_names = {room["room_id"]: room.get("name", "") for room in record.get("rooms", [])}
    assets = [["Gerät", "Typ / Modell", "Raum", "IP-Adresse", "Ports", "Strom"]]
    for asset in record.get("assets", []):
        ports = ", ".join(f"{row.get('protocol','tcp').upper()}/{row.get('port')}" for row in asset.get("ports", [])) or "–"
        power = f"{asset.get('power_w')} W" if asset.get("power_w") is not None else "unbekannt"
        assets.append([asset.get("name", ""), " ".join(part for part in (asset.get("kind", ""), asset.get("manufacturer", ""), asset.get("model", "")) if part), room_names.get(asset.get("room_id", ""), "–"), asset.get("ip", "–"), ports, power])
    if len(assets) > 1:
        story.append(_table(assets, [36*mm, 45*mm, 26*mm, 27*mm, 25*mm, 16*mm]))
    else:
        story.append(Paragraph("Keine Geräte erfasst.", styles["BodyText"]))
    if record.get("scans"):
        story.append(Paragraph("LAN-Scanprotokoll", styles["SOHeading"]))
        scan_rows = [["Zeitpunkt", "Zielnetz", "TCP-Ports", "Ergebnis"]]
        for scan in record.get("scans", []):
            scan_rows.append([scan.get("scanned_at", ""), scan.get("cidr", ""), ", ".join(str(port) for port in scan.get("ports", [])), f"{scan.get('device_count', 0)} Geräte / {scan.get('probed_hosts', 0)} geprüfte Hosts"])
        story.append(_table(scan_rows, [38*mm, 32*mm, 42*mm, 50*mm]))
        for scan in record.get("scans", []):
            story.append(Paragraph(_p("Einschränkungen: " + str(scan.get("limitations", ""))), styles["SOMuted"]))
    story.append(Paragraph("Verbindungen", styles["SOHeading"]))
    asset_names = {row["asset_id"]: row.get("name", "") for row in record.get("assets", [])}
    links = [["Von / Port", "Nach / Port", "Typ", "Kabel / Länge", "Protokoll / Status"]]
    for edge in record.get("connections", []):
        links.append([f"{asset_names.get(edge.get('from_asset'), 'Anschluss')} · {edge.get('from_port','')}", f"{asset_names.get(edge.get('to_asset'), 'Anschluss')} · {edge.get('to_port','')}", edge.get("kind", ""), f"{edge.get('cable_type','')} {edge.get('length_m','')} m", f"{edge.get('protocol','')} {edge.get('direction','')}"])
    if len(links) > 1: story.append(_table(links, [42*mm, 42*mm, 25*mm, 38*mm, 28*mm]))
    else: story.append(Paragraph("Keine Verbindungen erfasst.", styles["BodyText"]))
    story.append(Paragraph("Befunde und Begründungen", styles["SOHeading"]))
    finding_by_id = {row["finding_id"]: row for row in findings}
    edge_lines = {row["target_id"]: row for row in record.get("finding_edges", []) if row.get("target_id") in finding_by_id and row.get("source_id") in finding_by_id}
    for index, finding in enumerate(findings, 1):
        label = STATUS_LABELS.get(finding.get("status", ""), "Unbekannt")
        evidence = EVIDENCE_LABELS.get(finding.get("evidence_class", ""), "Unbekannt")
        heading = f"{index}. {_p(finding.get('title'))} · {escape(label)}"
        section = [Paragraph(heading, styles["SOFinding"]), Paragraph(_p(finding.get("description")), styles["BodyText"])]
        source_bits = [f"Evidenz: {evidence}", f"Zeit: {finding.get('observed_at') or 'unbekannt'}"]
        if finding.get("confidence"): source_bits.append(f"Vertrauen: {finding['confidence']} %")
        if finding.get("source"): source_bits.append(f"Quelle: {finding['source']}")
        predecessor = edge_lines.get(finding["finding_id"])
        if predecessor:
            relation = predecessor.get("relation", "verknüpft mit")
            parent = finding_by_id.get(predecessor.get("source_id"), {})
            source_bits.append(f"Beziehung: {parent.get('title', '')} → {relation} → {finding.get('title', '')}")
        custom = [f"{field.get('label')}: {finding.get('custom_values', {}).get(field.get('field_id', ''), '')}" for field in record.get("custom_field_definitions", []) if finding.get("custom_values", {}).get(field.get("field_id", ""))]
        if custom: source_bits.append("Zusatzangaben: " + "; ".join(custom))
        section.append(Paragraph(_p(" · ".join(source_bits)), styles["SOMuted"]))
        if finding.get("status") in {"unreviewed", "not_assessable"}:
            section.append(Paragraph("Hinweis: Dieser Befund ist nicht bestätigt und wird ausdrücklich mit seinem Prüfstatus wiedergegeben.", styles["SOMuted"]))
        story.append(KeepTogether(section))
    story.append(Paragraph("Elektrische Lastübersicht", styles["SOHeading"]))
    totals = SiteVisitStore.power_totals(record)
    if totals:
        power_rows = [["Stromkreis", "Bekannte Last", "Erfasste Grenze", "Rest / Status", "Unbekannt"]]
        for row in totals:
            limit = f"{row['limit_w']:.2f} W" if row["limit_w"] is not None else "unbekannt"
            remaining = f"{row['remaining_w']:.2f} W" if row["remaining_w"] is not None else "nicht bewertet"
            if row["over_limit"]: remaining = "ÜBER GRENZE: " + remaining
            power_rows.append([row["circuit"], f"{row['power_w']:.2f} W", limit, remaining, str(row["unknown_devices"])])
        story.append(_table(power_rows, [44*mm, 29*mm, 33*mm, 45*mm, 23*mm]))
        story.append(Paragraph("Überschlägige Summe aus erfassten Angaben; keine elektrische Sicherheitsprüfung oder Freigabe.", styles["SOMuted"]))
    if record.get("notes"):
        story.extend([Paragraph("Notizen", styles["SOHeading"]), Paragraph(_p(record["notes"]), styles["BodyText"])])
    attachments = [row for row in record.get("attachments", []) if row.get("target_id") in {record["visit_id"], *(finding["finding_id"] for finding in findings)}]
    if attachments:
        story.append(Paragraph("Beigefügte Dateien", styles["SOHeading"]))
        for item in attachments:
            story.append(Paragraph(f"{_p(item.get('filename'))} · {_p(item.get('content_type'))} · SHA-256 {_p(item.get('sha256'))}", styles["SOMuted"]))
    doc.build(story)
    payload = output.getvalue()
    return payload, hashlib.sha256(payload).hexdigest(), [row["finding_id"] for row in findings]
