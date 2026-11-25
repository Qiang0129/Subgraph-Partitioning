import os
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image

from app import vision


def test_ocr_windows_configuration_bgr_and_reuse(monkeypatch, tmp_path):
    cache = tmp_path / "models"
    monkeypatch.setenv("SFA_OCR_CACHE", str(cache))
    monkeypatch.delenv("PADDLE_PDX_CACHE_HOME", raising=False)
    monkeypatch.delenv("PADDLE_PDX_MODEL_SOURCE", raising=False)
    monkeypatch.delenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", raising=False)
    monkeypatch.setattr(vision, "_ocr_engine", None)
    calls, pixels = [], []

    class FakeOCR:
        def __init__(self, **kwargs):
            assert os.environ["PADDLE_PDX_CACHE_HOME"] == str(cache.resolve())
            calls.append(kwargs)

        def predict(self, image):
            pixels.append(image)
            return iter([{"rec_polys": [[[0, 0], [2, 0], [2, 1], [0, 1]]],
                          "rec_texts": ["HUVEC"], "rec_scores": [0.98]}])

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakeOCR))
    source = tmp_path / "中文原图.png"
    Image.new("RGB", (3, 2), (255, 10, 20)).save(source)
    for _ in range(2):
        result = vision.detect_text(source)
        assert result["available"] is True
        assert result["boxes"][0]["text"] == "HUVEC"
        assert result["boxes"][0]["rect"] == [0, 0, 3, 2]
    assert len(calls) == 1
    assert calls[0]["device"] == "cpu"
    assert calls[0]["enable_mkldnn"] is False
    assert calls[0]["cpu_threads"] == 4
    assert pixels[0].flags.c_contiguous
    np.testing.assert_array_equal(pixels[0][0, 0], [20, 10, 255])
    assert os.environ["PADDLE_PDX_MODEL_SOURCE"] == "bos"
    assert os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"


def test_ocr_respects_existing_cache_and_source(monkeypatch, tmp_path):
    monkeypatch.delenv("SFA_OCR_CACHE", raising=False)
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path / "existing"))
    monkeypatch.setenv("PADDLE_PDX_MODEL_SOURCE", "huggingface")
    assert vision._configure_ocr_environment() == tmp_path / "existing"
    assert os.environ["PADDLE_PDX_MODEL_SOURCE"] == "huggingface"


def test_ocr_failure_is_logged_and_not_success(monkeypatch, tmp_path, caplog):
    class BrokenOCR:
        def predict(self, image):
            raise NotImplementedError("inference diagnostic detail")

    monkeypatch.setattr(vision, "_ocr_engine", BrokenOCR())
    source = tmp_path / "source.png"
    Image.new("RGB", (3, 2)).save(source)
    result = vision.detect_text(source)
    assert result["available"] is False and result["boxes"] == []
    assert "NotImplementedError" in result["error"]
    assert "inference diagnostic detail" in caplog.text
    assert any(i["code"] == "OCR_UNAVAILABLE" for i in vision.check_layout(
        vision.Layout(image_size=[3, 2], panels=[]), result))


def test_read_image_normalizes_exif(tmp_path):
    source = tmp_path / "旋转.jpg"
    image = Image.new("RGB", (8, 4), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    normalized = vision.read_image(source)
    assert normalized.size == (4, 8)
    assert normalized.getexif().get(274, 1) == 1
