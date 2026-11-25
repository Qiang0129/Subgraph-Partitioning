import base64
import hashlib
import io
import json
import logging
import os
import re
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .models import Issue, Layout

Image.MAX_IMAGE_PIXELS = 40_000_000
_ocr_lock = threading.Lock()
_ocr_engine = None
_logger = logging.getLogger(__name__)


def _configure_ocr_environment() -> Path:
    """Configure PaddleX before its first import (it caches environment values)."""
    configured = os.environ.get("SFA_OCR_CACHE") or os.environ.get("PADDLE_PDX_CACHE_HOME")
    if configured:
        cache = Path(configured).expanduser().resolve()
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        cache = Path(local) / "SubgraphPartitioning" / "models"
    else:
        cache = Path.home() / ".cache" / "subgraph-partitioning" / "models"
    cache.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" and not str(cache).isascii():
        # Paddle's native model loader may fail on Unicode paths. Use the OS
        # short-path alias when available; never silently fall back to the repo.
        import ctypes
        from ctypes import wintypes

        get_short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short_path.restype = wintypes.DWORD
        size = get_short_path(str(cache), None, 0)
        if size:
            buffer = ctypes.create_unicode_buffer(size)
            length = get_short_path(str(cache), buffer, size)
            if 0 < length < size and buffer.value.isascii():
                cache = Path(buffer.value)
        if not str(cache).isascii():
            raise ValueError("Set SFA_OCR_CACHE to a writable ASCII-only absolute path")
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache)
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    return cache


def read_image(path: str | Path) -> Image.Image:
    with Image.open(path) as source:
        if source.format not in {"PNG", "JPEG"}:
            raise ValueError("Only PNG and JPEG are supported")
        return ImageOps.exif_transpose(source).convert("RGB")


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def data_image(image: Image.Image, limit: int = 1600) -> tuple[str, dict]:
    w, h = image.size
    display = image.copy()
    display.thumbnail((limit, limit))
    buffer = io.BytesIO()
    display.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(), {
        "original_size": [w, h], "display_size": list(display.size),
        "scale_to_original": [w / display.width, h / display.height],
    }


def detect_text(path: str | Path) -> dict:
    global _ocr_engine
    with _ocr_lock:
        try:
            if _ocr_engine is None:
                _configure_ocr_environment()
                try:
                    from paddleocr import PaddleOCR
                except ImportError:
                    _logger.exception("PaddleOCR or one of its dependencies could not be imported")
                    return {"available": False, "boxes": [], "error": "OCR_NOT_INSTALLED: install agent/requirements-ocr.txt"}
                _ocr_engine = PaddleOCR(lang="en", device="cpu", cpu_threads=4, enable_mkldnn=False,
                                       use_doc_orientation_classify=False,
                                       use_doc_unwarping=False, use_textline_orientation=True)
            # Decode Unicode filenames and normalize EXIF with Pillow; Paddle's
            # ndarray interface expects BGR, as with OpenCV-loaded images.
            image = np.array(read_image(path))[:, :, ::-1].copy()
            result = next(iter(_ocr_engine.predict(image)))
            boxes = []
            for poly, text, score in zip(result["rec_polys"], result["rec_texts"], result["rec_scores"]):
                points = np.asarray(poly).astype(float)
                boxes.append({"text": str(text), "score": float(score),
                              "rect": [int(points[:, 0].min()), int(points[:, 1].min()),
                                       int(points[:, 0].max()) + 1, int(points[:, 1].max()) + 1]})
            return {"available": True, "boxes": boxes}
        except Exception as error:
            _logger.exception("OCR inference failed")
            return {"available": False, "boxes": [],
                    "error": f"OCR_FAILED: {type(error).__name__}; see agent error log. "
                             "On Windows, set SFA_OCR_CACHE to a writable ASCII-only path if model loading fails."}


def structure(path: str) -> dict:
    image = np.array(read_image(path))
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    mask = gray < 235
    return {"image_size": [image.shape[1], image.shape[0]],
            "horizontal_whitespace_rows": np.where(mask.mean(axis=1) < .008)[0].tolist()[:1500],
            "vertical_whitespace_columns": np.where(mask.mean(axis=0) < .008)[0].tolist()[:1500],
            "note": "Whitespace is evidence, not a required global grid. Shared titles can cross columns."}


def check_layout(layout: Layout, text: dict | None = None) -> list[dict]:
    issues = []
    if not layout.panels:
        issues.append(Issue(code="NO_PANELS", severity="error", message="没有面板，不能确认或导出正式结果"))
    if not text or not text.get("available"):
        issues.append(Issue(code="OCR_UNAVAILABLE", severity="warning", message="未获得有效 OCR，不能自动通过"))
    else:
        for region in layout.panels + layout.shared_regions:
            x1, y1, x2, y2 = region.rect
            for word in text.get("boxes", []):
                a, b, c, d = word["rect"]
                intersection = max(0, min(x2, c) - max(x1, a)) * max(0, min(y2, d) - max(y1, b))
                area = max(1, (c-a)*(d-b))
                if .04 < intersection / area < .96:
                    issues.append(Issue(code="TEXT_INTERSECTION", severity="warning", region_id=region.id,
                                        message=f"边界疑似切过文字：{word['text'][:80]}"))
    for i, first in enumerate(layout.panels):
        for second in layout.panels[i+1:]:
            a,b,c,d = first.rect
            e,f,g,h = second.rect
            if min(c,g) > max(a,e) and min(d,h) > max(b,f):
                issues.append(Issue(code="PANEL_OVERLAP", severity="warning", region_id=first.id,
                                    message=f"面板 {first.label}/{second.label} 重叠，需要确认内容归属"))
    return [issue.model_dump() for issue in issues]


def crop_layout(source: str, layout: Layout, target: Path, text: dict | None = None) -> dict:
    image = read_image(source)
    if tuple(layout.image_size) != image.size:
        raise ValueError("Layout dimensions do not match normalized source")
    target.mkdir(parents=True, exist_ok=False)
    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    paths = {}
    for shared, regions in ((False, layout.panels), (True, layout.shared_regions)):
        for region in regions:
            name = f"shared_{region.id}.png" if shared else f"panel_{region.id}.png"
            image.crop(region.rect).save(target / name)
            paths[region.id] = name
            color = (180, 80, 18) if shared else (13, 105, 114)
            draw.rectangle(region.rect, outline=color, width=max(2, image.width // 500))
            draw.text((region.rect[0]+3, region.rect[1]+3), region.id, fill=color)
    preview.save(target / "preview.png")
    payload = layout.model_dump()
    payload.update({"source": {"name": Path(source).name, "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest()},
                    "files": paths, "coordinate_system": "normalized source pixels; [x1,y1,x2,y2)"})
    atomic_json(target / "layout.json", payload)
    report = {"issues": check_layout(layout, text), "status": "DRAFT", "pixel_crop": True}
    atomic_json(target / "check_report.json", report)
    return {"directory": str(target), "layout": payload, "report": report}


def import_legacy(source: str, path: Path) -> Layout:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    size = list(read_image(source).size)
    if raw["image_size"] != size:
        raise ValueError("Legacy layout image size mismatch")
    expected_name = re.split(r"[\\/]", raw.get("source", ""))[-1]
    if expected_name and expected_name.casefold() != Path(source).name.casefold():
        raise ValueError("Legacy source name mismatch")
    return Layout(image_size=size, panels=[{"id": f"p_{label}", "label": label, "rect": item["rect"]}
                                          for label, item in raw["panels"].items()])
