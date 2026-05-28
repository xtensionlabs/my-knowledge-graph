"""OCR — image → text dispatcher.

Strategy (per `feedback-claude-first` memory):
    1. Always try **Tesseract** first (local, free, no API spend).
    2. If Tesseract returns fewer than `OCR_VISION_FALLBACK_MIN_CHARS` characters,
       the image is probably a diagram / heavily visual. Escalate to Claude
       Vision (`VISION_MODEL`) which can reason about layout, not just glyphs.
    3. Write the extracted text into the inbox as a normal capture with
       source = "ocr" (already on the VALID_CAPTURE_SOURCES whitelist).

Tesseract must be installed on the host (https://tesseract-ocr.github.io/).
The user is on Windows; `pytesseract.pytesseract.tesseract_cmd` honors the
PATH or can be overridden via the `TESSERACT_CMD` env var if Synapse is
launched without admin install.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from synapse.capture.inbox import write_to_inbox
from synapse.config import (
    ANTHROPIC_MAX_TOKENS,
    OCR_VISION_FALLBACK_MIN_CHARS,
    VISION_MODEL,
)
from synapse.llm.client import ClaudeClient, StructuredOutputError, claude


_SUPPORTED_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp")


class OCRError(Exception):
    """Raised on unrecoverable OCR failures."""


@dataclass
class OCRResult:
    """Bundled OCR output."""

    text: str
    method: str           # "tesseract" | "vision" | "tesseract+vision"
    image_path: Path
    inbox_path: Path | None = None
    cost_usd: float = 0.0


# ── Tesseract path ───────────────────────────────────────────────────────────


def _run_tesseract(image_path: Path) -> str:
    """Return text via pytesseract. Returns empty string on failure (never raises)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover — deps are pinned
        raise OCRError(f"OCR deps missing: {exc}") from exc

    # Optional override for the tesseract binary path.
    custom = os.environ.get("TESSERACT_CMD")
    if custom:
        pytesseract.pytesseract.tesseract_cmd = custom

    try:
        with Image.open(image_path) as img:
            text = pytesseract.image_to_string(img)
        return text.strip()
    except FileNotFoundError as exc:
        raise OCRError(
            "tesseract binary not found on PATH. "
            "Install from https://tesseract-ocr.github.io/ or set TESSERACT_CMD."
        ) from exc
    except Exception as exc:  # noqa: BLE001 — log + fall back to vision
        logger.warning("tesseract failed on {p}: {exc}", p=image_path, exc=exc)
        return ""


# ── Vision (Claude) path ─────────────────────────────────────────────────────


class _VisionOCROutput(BaseModel):
    """Strict schema for the vision OCR call's JSON."""

    confidence: float = Field(ge=0.0, le=1.0)
    extracted_text: str
    image_kind: str = "unknown"  # diagram | screenshot | photo | handwriting | unknown
    spatial_description: str = ""  # for diagrams: describe the layout in prose


_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


async def _run_vision(
    image_path: Path,
    *,
    client: ClaudeClient | None = None,
) -> tuple[str, float]:
    """Vision-based OCR via Claude. Returns (text, cost_usd)."""
    cl = client or claude
    mime = _MIME_BY_EXT.get(image_path.suffix.lower(), "image/png")
    image_bytes = image_path.read_bytes()
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    # Direct messages.create call — the structured wrapper takes a text-only
    # prompt; vision messages need the image content block format.
    cl._ensure_client()  # type: ignore[attr-defined]
    assert cl._client is not None  # type: ignore[attr-defined]

    response = await cl._client.messages.create(  # type: ignore[attr-defined]
        model=VISION_MODEL,
        max_tokens=ANTHROPIC_MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract all readable text from this image. "
                            "If it's a diagram or schematic, also describe the spatial relationships "
                            "in prose (e.g., 'node A connects to node B via an arrow labeled X'). "
                            "Return ONLY valid JSON: "
                            '{"confidence": 0.0-1.0, "extracted_text": "...", '
                            '"image_kind": "diagram|screenshot|photo|handwriting|unknown", '
                            '"spatial_description": "..."}. '
                            "Begin with `{`."
                        ),
                    },
                ],
            }
        ],
    )

    # Parse usage
    usage = response.usage
    in_tok = getattr(usage, "input_tokens", 0)
    out_tok = getattr(usage, "output_tokens", 0)
    from synapse.config import MODEL_PRICING_USD_PER_MTOK
    pricing = MODEL_PRICING_USD_PER_MTOK.get(VISION_MODEL, (0.0, 0.0))
    cost = (in_tok * pricing[0] + out_tok * pricing[1]) / 1_000_000.0

    # Extract text content
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    raw = "".join(parts)

    # Parse JSON output
    import json
    import re
    candidate = raw.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL)
        if m:
            payload = json.loads(m.group(1))
        else:
            raise OCRError(f"vision OCR returned non-JSON: {raw[:200]}") from None

    text_out = payload.get("extracted_text", "")
    spatial = payload.get("spatial_description", "")
    image_kind = payload.get("image_kind", "unknown")
    if spatial:
        text_out = f"{text_out}\n\n[layout]\n{spatial}".strip()
    if image_kind and image_kind != "unknown":
        text_out = f"[image_kind={image_kind}]\n{text_out}"
    return text_out, cost


# ── Public entry point ───────────────────────────────────────────────────────


async def ocr_to_inbox(
    image_path: Path,
    *,
    write_inbox: bool = True,
    client: ClaudeClient | None = None,
) -> OCRResult:
    """OCR an image and (optionally) write the extracted text into the inbox.

    Args:
        image_path: Path to the image on disk.
        write_inbox: If True, drop the extracted text into `inbox/` as a
            `source=ocr` capture. Default True.
        client: Optional ClaudeClient (tests inject a mock).

    Returns:
        OCRResult with text, method used, and (if `write_inbox`) the inbox path.

    Raises:
        OCRError: If neither Tesseract nor Vision can produce text, or if
                  the file extension is unsupported.
    """
    if not image_path.is_file():
        raise OCRError(f"image not found: {image_path}")
    if image_path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
        raise OCRError(f"unsupported image type: {image_path.suffix}")

    # Always try Tesseract first.
    text = _run_tesseract(image_path)
    method = "tesseract"
    cost = 0.0

    # Fall back to vision if Tesseract didn't get enough out.
    if len(text) < OCR_VISION_FALLBACK_MIN_CHARS:
        logger.info(
            "ocr: tesseract returned {n} chars; falling back to vision",
            n=len(text),
        )
        try:
            vision_text, cost = await _run_vision(image_path, client=client)
        except OCRError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OCRError(f"vision OCR failed: {exc}") from exc

        if text:
            text = f"{text}\n\n---\n{vision_text}".strip()
            method = "tesseract+vision"
        else:
            text = vision_text
            method = "vision"

    if not text.strip():
        raise OCRError("both tesseract and vision returned empty text")

    inbox_path: Path | None = None
    if write_inbox:
        title = f"OCR: {image_path.name}"
        inbox_path = write_to_inbox(
            source="ocr",
            content=text,
            extra={"title": title, "ocr_source_image": str(image_path), "ocr_method": method},
        )

    return OCRResult(text=text, method=method, image_path=image_path, inbox_path=inbox_path, cost_usd=cost)
