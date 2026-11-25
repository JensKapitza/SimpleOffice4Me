"""Preserve dotless i from local OCR without shipping an additional font."""
from __future__ import annotations

from reportlab.pdfbase.pdfmetrics import Encoding, Font, registerEncoding, registerFont
from reportlab.pdfbase.rl_codecs import RL_Codecs

_OCR_ENCODING = "SimpleOfficeOCRLatinEncoding"
_OCR_LATIN_FONT = "SimpleOfficeOCRHelveticaLatin"
_DOTLESS_I = "ı"
_DOTLESS_I_CODE = 0x81


def _register_ocr_latin_font() -> None:
    # Standard Helvetica has the dotlessi glyph, but WinAnsi doesn't select
    # it. Add a private encoding that overlays a redundant bullet slot;
    # the standard bullet still uses its canonical encoding.
    encoding = Encoding(_OCR_ENCODING, "WinAnsiEncoding")
    encoding[_DOTLESS_I_CODE] = "dotlessi"
    registerEncoding(encoding)
    decoded = {value: bytes([value]).decode("WinAnsiEncoding") for value in range(32, 256)}
    reader_map = {value: ord(character) for value, character in decoded.items()}
    writer_map = {ord(character): character.encode("WinAnsiEncoding")[0] for character in set(decoded.values())}
    reader_map[_DOTLESS_I_CODE] = ord(_DOTLESS_I)
    writer_map[ord(_DOTLESS_I)] = _DOTLESS_I_CODE
    # ReportLab chooses the synonymous 0x7f byte for a bullet. Use the
    # explicit WinAnsi bullet so extractors agree on this mixed line as well.
    writer_map[ord("•")] = 0x95
    RL_Codecs.add_dynamic_codec(_OCR_ENCODING, writer_map, reader_map)
    registerFont(Font(_OCR_LATIN_FONT, "Helvetica", _OCR_ENCODING))


def ocr_latin_font(text: str) -> str:
    return _OCR_LATIN_FONT if _DOTLESS_I in text else "Helvetica"


_register_ocr_latin_font()
