"""Render SOFP peer payloads as SVG QR images."""
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing


def render_qr_svg(payload):
    widget = QrCodeWidget(str(payload))
    bounds = widget.getBounds()
    width = max(1, bounds[2] - bounds[0])
    height = max(1, bounds[3] - bounds[1])
    size = 220
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)
    return renderSVG.drawToString(drawing)
