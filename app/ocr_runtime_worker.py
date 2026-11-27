"""Private line-oriented RapidOCR worker for the isolated managed runtime."""
from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

MAX_BLOCKS = 250
MAX_TEXT = 20_000
OCR_RENDER_EDGE = 3508


def _engine():
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    from importlib.util import find_spec
    spec = find_spec("rapidocr")
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError("RapidOCR package missing")
    model_root = Path(next(iter(spec.submodule_search_locations))) / "models"
    paths = {
        "det": model_root / "PP-OCRv6_det_small.onnx",
        "cls": model_root / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "rec": model_root / "PP-OCRv6_rec_small.onnx",
    }
    if any(not path.is_file() for path in paths.values()):
        raise FileNotFoundError("Bundled OCR model missing")
    from rapidocr import RapidOCR
    return RapidOCR(params={
        "Global.model_root_dir": str(model_root),
        "Global.max_side_len": OCR_RENDER_EDGE,
        "Det.lang_type": "german",
        "Det.model_path": str(paths["det"]),
        "Cls.model_path": str(paths["cls"]),
        "Rec.lang_type": "german",
        "Rec.model_path": str(paths["rec"]),
    })


def _result(engine, path: str) -> dict:
    result = engine(path)
    def values(name: str) -> list:
        value = getattr(result, name, None)
        return [] if value is None else list(value)

    # RapidOCR returns NumPy arrays; ``array or []`` raises because arrays
    # deliberately have no single truth value.
    texts = values("txts")
    scores = values("scores")
    boxes = values("boxes")
    blocks = []
    for index, raw in enumerate(texts[:MAX_BLOCKS]):
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        try:
            score = round(max(0.0, min(float(scores[index]), 1.0)), 4) if index < len(scores) else None
        except (TypeError, ValueError):
            score = None
        points = []
        if index < len(boxes):
            try:
                points = [[round(float(point[0]), 1), round(float(point[1]), 1)]
                          for point in boxes[index] if len(point) >= 2]
            except (TypeError, ValueError, IndexError):
                points = []
        blocks.append({"text": text, "confidence": score, "box": points})
    text = "\n".join(block["text"] for block in blocks)[:MAX_TEXT]
    confidence = [block["confidence"] for block in blocks if block["confidence"] is not None]
    return {"engine": "rapidocr", "status": "completed", "text": text, "characters": len(text),
            "confidence": round(sum(confidence) / len(confidence), 4) if confidence else None,
            "blocks": blocks}


def _smoke() -> dict:
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1800, 400), "white")
    draw = ImageDraw.Draw(image)
    candidates = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf")
    font = next((ImageFont.truetype(item, 180) for item in candidates if Path(item).is_file()),
                ImageFont.load_default(size=180))
    draw.text((60, 70), "SO4ME OCR 123", font=font, fill="black")
    descriptor, filename = tempfile.mkstemp(prefix="simpleoffice-ocr-smoke-", suffix=".png")
    os.close(descriptor)
    path = Path(filename)
    try:
        image.save(path)
        output = _result(_engine(), str(path))
    finally:
        path.unlink(missing_ok=True)
    output["ok"] = any(token in output["text"].upper() for token in ("SO4ME", "OCR", "123"))
    return output


def main() -> int:
    logging.disable(logging.CRITICAL)
    if sys.argv[1:] == ["--smoke"]:
        with contextlib.redirect_stdout(sys.stderr):
            output = _smoke()
        print(json.dumps(output, ensure_ascii=False), flush=True)
        return 0 if output.get("ok") else 2
    with contextlib.redirect_stdout(sys.stderr):
        engine = _engine()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            path = request.get("path") if isinstance(request, dict) else None
            if not isinstance(path, str) or not path or len(path) > 4096 or "\x00" in path:
                raise ValueError("invalid image path")
            image = Path(path)
            if not image.is_file():
                raise ValueError("image unavailable")
            with contextlib.redirect_stdout(sys.stderr):
                output = _result(engine, str(image))
        except Exception:
            output = {"engine": "rapidocr", "status": "failed", "text": "", "characters": 0,
                      "confidence": None, "blocks": [], "error": "RapidOCR konnte das Bild nicht verarbeiten"}
        print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
