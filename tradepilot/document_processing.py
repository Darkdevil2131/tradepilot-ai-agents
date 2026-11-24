from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from typing import Any

from PIL import Image
from pypdf import PdfReader


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
SUPPORTED_CONTENT_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/tiff"}


class DocumentProcessingError(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedDocument:
    result: dict[str, Any]


def _normalise_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _keyword_indicators(text: str) -> dict[str, list[str]]:
    lowered = text.casefold()
    signature = sorted({term for term in ("signed by", "signature", "digitally signed") if term in lowered})
    stamp = sorted({term for term in ("stamp", "seal", "chop") if term in lowered})
    return {"signature_keywords": signature, "stamp_keywords": stamp}


def _pdf_tables(data: bytes) -> list[dict[str, Any]]:
    try:
        import pdfplumber
    except ImportError:
        return []
    tables: list[dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(data)) as document:
        for page_number, page in enumerate(document.pages, start=1):
            for table_number, rows in enumerate(page.extract_tables() or [], start=1):
                cleaned = [[cell.strip() if isinstance(cell, str) else cell for cell in row] for row in rows]
                tables.append({"page": page_number, "table": table_number, "rows": cleaned})
    return tables


def _pdf_signature_fields(reader: PdfReader) -> list[dict[str, Any]]:
    fields = reader.get_fields() or {}
    found = []
    for name, field in fields.items():
        if str(field.get("/FT", "")) == "/Sig":
            found.append({
                "field": name,
                "value_present": bool(field.get("/V")),
                "cryptographic_validity": "not_verified",
            })
    return found


def _extract_pdf(data: bytes) -> dict[str, Any]:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:
        raise DocumentProcessingError("The PDF could not be parsed safely") from exc
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            return {
                "text": "", "tables": [], "pages": len(reader.pages),
                "encrypted": True, "warnings": ["Encrypted PDF requires an authorized password."],
                "signature_fields": [], "ocr_used": False,
            }
    pages = []
    warnings = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
            warnings.append("One PDF page could not be text-extracted.")
    text = _normalise_text("\n".join(pages))
    if not text:
        warnings.append("No embedded text was found. Submit rendered page images for OCR.")
    return {
        "text": text,
        "tables": _pdf_tables(data),
        "pages": len(reader.pages),
        "encrypted": bool(reader.is_encrypted),
        "warnings": warnings,
        "signature_fields": _pdf_signature_fields(reader),
        "ocr_used": False,
    }


def _extract_image(data: bytes) -> dict[str, Any]:
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise DocumentProcessingError("The image could not be decoded safely") from exc
    try:
        import pytesseract

        text = _normalise_text(pytesseract.image_to_string(image, config="--psm 6"))
        warning = [] if text else ["OCR completed but no text was recognized."]
        ocr_used = True
    except Exception:
        text = ""
        warning = ["OCR engine is unavailable; install the Tesseract binary to process scanned images."]
        ocr_used = False
    return {
        "text": text, "tables": [], "pages": 1, "encrypted": False,
        "warnings": warning, "signature_fields": [], "ocr_used": ocr_used,
        "image": {"width": image.width, "height": image.height, "format": image.format or "decoded"},
    }


def process_document_bytes(data: bytes, filename: str, content_type: str) -> dict[str, Any]:
    if not data:
        raise DocumentProcessingError("The uploaded document is empty")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentProcessingError("Document exceeds the 10 MiB limit")
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise DocumentProcessingError("Only PDF, PNG, JPEG, and TIFF documents are supported")
    extracted = _extract_pdf(data) if content_type == "application/pdf" else _extract_image(data)
    indicators = _keyword_indicators(extracted["text"])
    signature_evidence = bool(extracted["signature_fields"] or indicators["signature_keywords"])
    stamp_evidence = bool(indicators["stamp_keywords"])
    return {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        **extracted,
        "indicators": {
            **indicators,
            "signature_presence_indicated": signature_evidence,
            "stamp_presence_indicated": stamp_evidence,
        },
        "authenticity": {
            "status": "not_verified",
            "reason": "Content extraction, hashes, visible keywords, and PDF signature fields cannot prove document authenticity.",
            "cryptographic_validation_performed": False,
        },
        "provenance": "USER_PROVIDED",
    }
