"""Local real-image regression for panel-owned text; no model/API calls.

Uses the user's read-only local 15/a2 originals and the saved 45 layout. Results
and exact pixel crops go only into ignored runtime/text-policy/<timestamp>/.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from PIL import Image, ImageChops

from app.models import Layout
from app.vision import atomic_json, crop_layout, detect_text, import_legacy, read_image


def main(cached=False, ocr_dir=None):
    target = ROOT / "runtime" / "text-policy" / str(int(time.time()))
    target.mkdir(parents=True)
    results = []
    saved_45 = None
    db = ROOT / "runtime" / "app.sqlite"
    if db.exists():
        with sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True) as connection:
            for (payload,) in connection.execute("SELECT payload FROM objects WHERE kind='image' ORDER BY rowid DESC"):
                item = json.loads(payload)
                if item.get("name") == "45.png" and item.get("layout", {}).get("panels"):
                    saved_45 = item
                    break
    for name in ("15", "a2", "45"):
        source = ROOT.parent / "图片" / f"{name}.png"
        if name == "45":
            if not saved_45:
                results.append({"name": name, "skipped": "No local saved 45 layout"})
                continue
            layout = Layout.model_validate(saved_45["layout"])
            cache = ROOT / "runtime" / "images" / saved_45["id"] / "agent" / "state.json"
        else:
            layout = import_legacy(str(source), ROOT.parent / "子图划分结果" / "result12" / name / "layout.json")
            cache = ROOT / "runtime" / "ocr-verification" / f"{name}.json"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        started = time.monotonic()
        if ocr_dir:
            text = json.loads((Path(ocr_dir) / f"{name}-ocr.json").read_text("utf-8"))
        else:
            text = json.loads(cache.read_text("utf-8"))["ocr"] if cached else detect_text(source)
        assert text["available"] and text["boxes"], f"{name}: OCR unavailable"
        atomic_json(target / f"{name}-ocr.json", text)
        result = crop_layout(str(source), layout, target / name, text)
        text_issues = [issue for issue in result["report"]["issues"] if issue["code"] == "TEXT_INTERSECTION"]
        if name == "15":
            assert not any(title in issue["message"] for issue in text_issues for title in ("MC-3T3-E1", "HUVEC"))
            assert not list((target / name).glob("shared_*.png"))
        elif name == "a2":
            for label in ("DOX+PD-1", "MDP+PD-1"):
                assert any(label in issue["message"] and issue["region_id"] == "p_d" for issue in text_issues)
        else:
            assert not any(issue["message"].endswith(": " + label) or issue["message"].endswith("：" + label)
                           for issue in text_issues for label in ("f", "h", "8.0", "Time (min)"))
            assert any("Concentration" in issue["message"] and issue["region_id"] == "p_c" for issue in text_issues)
        original = read_image(source)
        for panel in layout.panels:
            with Image.open(target / name / f"panel_{panel.id}.png") as actual:
                assert ImageChops.difference(actual.convert("RGB"), original.crop(panel.rect)).getbbox() is None
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        results.append({"name": name, "sha256": before, "source_unchanged": True,
                        "ocr_boxes": len(text["boxes"]), "seconds": round(time.monotonic()-started, 2),
                        "pixel_exact_panels": len(layout.panels), "issues": result["report"]["issues"]})
    report = {"fresh_ocr": not cached and not ocr_dir, "ocr_source": str(ocr_dir) if ocr_dir else "legacy cache" if cached else "PaddleOCR",
              "model_calls": 0, "results": results,
              "scope": "local known examples only; not an overall quality/accuracy evaluation"}
    atomic_json(target / "result.json", report)
    print(json.dumps({"directory": str(target), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--cached", action="store_true", help="Use previous local OCR boxes instead of fresh PaddleOCR")
    inputs.add_argument("--ocr-dir", type=Path, help="Reuse OCR JSON files from a prior check_text_policy run")
    args = parser.parse_args()
    main(args.cached, args.ocr_dir)
