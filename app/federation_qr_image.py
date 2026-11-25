"""Render SOFP peer payloads as SVG QR images."""
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing


def render_qr_svg(payload, size=220):
    widget = QrCodeWidget(str(payload))
    bounds = widget.getBounds()
    width = max(1, bounds[2] - bounds[0])
    height = max(1, bounds[3] - bounds[1])
    size = max(180, min(int(size), 640))
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)
    return renderSVG.drawToString(drawing)


def unavailable_qr_svg():
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240" '
        'role="img" aria-label="Federation QR nicht verfügbar"><rect width="240" height="240" fill="white"/>'
        '<rect x="1" y="1" width="238" height="238" fill="none" stroke="#adb5bd" stroke-width="2"/>'
        '<text x="120" y="112" text-anchor="middle" font-family="sans-serif" font-size="14" fill="#495057">'
        'QR nicht verfügbar</text><text x="120" y="134" text-anchor="middle" font-family="sans-serif" '
        'font-size="11" fill="#6c757d">Public-URL konfigurieren</text></svg>'
    )
