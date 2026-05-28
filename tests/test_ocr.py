"""OCR tests — Tesseract path, Vision fallback, inbox routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synapse.capture import ocr as ocr_module
from synapse.capture.ocr import OCRError, OCRResult, ocr_to_inbox


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_fake_image(tmp_path: Path, name: str = "shot.png") -> Path:
    """Create a minimal valid PNG (single black pixel) so PIL.Image.open works."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    path = tmp_path / name
    img = Image.new("RGB", (10, 10), color="white")
    img.save(path, "PNG")
    return path


# ── Tesseract path (mocked) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ocr_uses_tesseract_when_text_is_long(tmp_path: Path) -> None:
    img = _make_fake_image(tmp_path)
    long_text = "This is a long block of recognized text that exceeds the fallback threshold easily."
    with patch.object(ocr_module, "_run_tesseract", return_value=long_text):
        result = await ocr_to_inbox(img, write_inbox=False)
    assert result.method == "tesseract"
    assert long_text in result.text
    assert result.cost_usd == 0.0
    assert result.inbox_path is None


@pytest.mark.asyncio
async def test_ocr_writes_to_inbox(tmp_path: Path) -> None:
    img = _make_fake_image(tmp_path)
    with patch.object(ocr_module, "_run_tesseract", return_value="long enough recognised text body" * 3):
        result = await ocr_to_inbox(img, write_inbox=True)
    assert result.inbox_path is not None
    assert result.inbox_path.exists()
    assert "long enough" in result.inbox_path.read_text(encoding="utf-8")


# ── Vision fallback (mocked) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ocr_falls_back_to_vision_when_tesseract_returns_little(tmp_path: Path) -> None:
    img = _make_fake_image(tmp_path)

    async def fake_vision(image_path, *, client=None):
        return ("vision-extracted long text describing the diagram", 0.003)

    with patch.object(ocr_module, "_run_tesseract", return_value=""), \
         patch.object(ocr_module, "_run_vision", new=fake_vision):
        result = await ocr_to_inbox(img, write_inbox=False)
    assert result.method == "vision"
    assert "vision-extracted" in result.text
    assert result.cost_usd > 0.0


@pytest.mark.asyncio
async def test_ocr_combines_tesseract_and_vision_when_both_produce_output(tmp_path: Path) -> None:
    img = _make_fake_image(tmp_path)

    async def fake_vision(image_path, *, client=None):
        return ("vision text", 0.001)

    with patch.object(ocr_module, "_run_tesseract", return_value="short text"), \
         patch.object(ocr_module, "_run_vision", new=fake_vision):
        result = await ocr_to_inbox(img, write_inbox=False)
    assert result.method == "tesseract+vision"
    assert "short text" in result.text
    assert "vision text" in result.text


# ── Error paths ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ocr_rejects_unsupported_extension(tmp_path: Path) -> None:
    bad = tmp_path / "doc.pdf"
    bad.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(OCRError, match="unsupported"):
        await ocr_to_inbox(bad)


@pytest.mark.asyncio
async def test_ocr_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OCRError, match="not found"):
        await ocr_to_inbox(tmp_path / "nope.png")


@pytest.mark.asyncio
async def test_ocr_raises_when_both_methods_empty(tmp_path: Path) -> None:
    img = _make_fake_image(tmp_path)

    async def fake_vision(image_path, *, client=None):
        return ("", 0.0)

    with patch.object(ocr_module, "_run_tesseract", return_value=""), \
         patch.object(ocr_module, "_run_vision", new=fake_vision):
        with pytest.raises(OCRError, match="empty"):
            await ocr_to_inbox(img)
