"""PDF output for the library's existing Brother-compatible barcode labels."""
from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .printer import LABELS, render_barcode, render_qr_code


def book_labels_pdf(
    labels: list[tuple[str, str]], settings: dict[str, Any], *, style: str,
    layout: str = "media",
) -> bytes:
    """Render labels on configured media or standard A4 3-by-7 sheets."""
    if style not in {"with_text", "barcode_only", "qr_with_text", "qr_only"}:
        raise ValueError("Unbekannte Etikettenart")
    if layout not in {"media", "a4_3x7"}:
        raise ValueError("Unbekanntes Etikettenlayout")
    media = LABELS.get(str(settings.get("label", "62")).lower())
    if media is None:
        raise ValueError("Unbekannte Etikettengröße")

    output = BytesIO()
    document = canvas.Canvas(output, pageCompression=1, invariant=1)
    if layout == "a4_3x7":
        _draw_a4_sheet(document, labels, settings, style)
        document.save()
        return output.getvalue()

    page_width = media.width_mm * mm
    for value, caption in labels:
        qr_style = style.startswith("qr_")
        image = (render_qr_code if qr_style else render_barcode)(
            value,
            settings,
            caption=caption if style in {"with_text", "qr_with_text"} else "",
            show_text=style in {"with_text", "qr_with_text"},
        )
        with image:
            grayscale = image.convert("L")
            with grayscale:
                if media.endless:
                    page_height = page_width * grayscale.height / grayscale.width
                else:
                    page_height = media.length_mm * mm
                document.setPageSize((page_width, page_height))
                image_reader = ImageReader(grayscale)
                scale = min(page_width / grayscale.width, page_height / grayscale.height)
                draw_width = grayscale.width * scale
                draw_height = grayscale.height * scale
                document.drawImage(
                    image_reader,
                    (page_width - draw_width) / 2,
                    (page_height - draw_height) / 2,
                    width=draw_width,
                    height=draw_height,
                    preserveAspectRatio=True,
                    mask="auto",
                )
                document.showPage()
    document.save()
    return output.getvalue()


def _draw_a4_sheet(document, labels, settings, style):
    """Place up to 21 labels per A4 sheet using the common 63.5 x 38.1 mm grid."""
    page_width, page_height = 210 * mm, 297 * mm
    columns, rows = 3, 7
    cell_width, cell_height = 63.5 * mm, 38.1 * mm
    left = (page_width - columns * cell_width) / 2
    top = (page_height - rows * cell_height) / 2
    document.setPageSize((page_width, page_height))
    for index, (value, caption) in enumerate(labels):
        if index and index % (columns * rows) == 0:
            document.showPage()
            document.setPageSize((page_width, page_height))
        qr_style = style.startswith("qr_")
        image = (render_qr_code if qr_style else render_barcode)(
            value,
            settings,
            caption=caption if style in {"with_text", "qr_with_text"} else "",
            show_text=style in {"with_text", "qr_with_text"},
        )
        column, row = index % columns, (index // columns) % rows
        x = left + column * cell_width
        y = page_height - top - (row + 1) * cell_height
        with image:
            grayscale = image.convert("L")
            with grayscale:
                scale = min(cell_width / grayscale.width, cell_height / grayscale.height)
                draw_width = grayscale.width * scale
                draw_height = grayscale.height * scale
                document.drawImage(
                    ImageReader(grayscale),
                    x + (cell_width - draw_width) / 2,
                    y + (cell_height - draw_height) / 2,
                    width=draw_width,
                    height=draw_height,
                    preserveAspectRatio=True,
                    mask="auto",
                )
    document.showPage()
