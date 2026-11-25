"""Shared boundaries for PDF reader and conversion operations."""

MAX_PDF_BYTES = 1024 * 1024 * 1024


class PdfConversionTimeout(RuntimeError):
    """A local PDF export subprocess reached its existing time limit."""
