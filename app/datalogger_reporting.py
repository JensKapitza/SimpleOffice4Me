"""Shared, bounded presentation for a channel view and its exports."""

import csv
import io
from textwrap import wrap
from datetime import date, datetime, timedelta, timezone
from statistics import mean

EXPORT_ROWS = 10_000
MAX_RANGE_DAYS = 366


def date_window(start, end):
    """Return the inclusive first and exclusive last UTC instants."""
    if not start and not end:
        return None, None
    if not start or not end:
        raise ValueError("Bitte Anfang und Ende des Zeitraums angeben.")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if start != first.isoformat() or end != last.isoformat():
            raise ValueError
        after_last = last + timedelta(days=1)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Bitte einen gültigen UTC-Zeitraum angeben.") from exc
    if not 0 < (after_last - first).days <= MAX_RANGE_DAYS:
        raise ValueError("Der Zeitraum darf höchstens 366 Tage umfassen.")
    return f"{first.isoformat()}T00:00:00+00:00", f"{after_last.isoformat()}T00:00:00+00:00"


def summarize(rows):
    values = [row['value'] for row in rows if row.get('quality') == 'good']
    return {
        'rows': len(rows), 'valid': len(values),
        'not_ok': len(rows) - len(values),
        'min': min(values, default=None), 'max': max(values, default=None),
        'mean': mean(values) if values else None,
    }


def _safe_cell(value):
    """Display data in spreadsheets without interpreting untrusted labels as formulas."""
    text = str(value or "")
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


def csv_content(channel, rows):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(('time_utc', 'channel_id', 'channel', 'source', 'source_kind', 'unit', 'value', 'status', 'error_code'))

    def as_utc(row):
        timestamp = datetime.fromisoformat(row['measured_at'])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    for row in sorted(rows, key=as_utc):
        writer.writerow((as_utc(row).isoformat(), _safe_cell(channel['channel_id']),
                         _safe_cell(channel['name']), _safe_cell(row['source_name']),
                         _safe_cell(row['source_kind']), _safe_cell(channel['unit']),
                         row['value'], _safe_cell(row['quality']), _safe_cell(row.get('error_code'))))
    return out.getvalue()


def summary_pdf(lines):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    buf = io.BytesIO()
    canvas = Canvas(buf, pagesize=A4, invariant=True)
    canvas.setTitle('Datenlogger – Messbericht')
    y = 785
    canvas.setFont('Helvetica', 11)
    for line in lines:
        for part in wrap(' '.join(str(line).split()), width=83) or ['']:
            if y < 55:
                canvas.showPage()
                canvas.setFont('Helvetica', 11)
                y = 785
            canvas.drawString(45, y, part)
            y -= 17
    canvas.save()
    return buf.getvalue()
