"""PDF pipeline: browser-impersonating download, extraction, cleanup, fallback."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from artikel_mcp.http import HttpClient, get_client

logger = logging.getLogger("artikel_mcp.pdf")

PDF_MAGIC = b"%PDF"
_UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"


class PdfError(RuntimeError):
    """Raised when a PDF cannot be downloaded, resolved, or extracted."""


@dataclass
class PdfResult:
    pdf_url: str
    markdown: str
    used_fallback: bool = False
    via_unpaywall: bool = False


def download_pdf(url: str, client: HttpClient | None = None) -> tuple[bytes, bool]:
    """Download PDF bytes via impersonating client.

    Returns (bytes, used_unpaywall_fallback). Raises PdfError on non-PDF content.
    """
    client = client or get_client()
    body = client.get(url, timeout=120)
    if not body.startswith(PDF_MAGIC):
        logger.warning("non-PDF magic bytes from %s", url)
        raise PdfError(f"expected PDF from {url}, got non-PDF content")
    logger.info("downloaded PDF %s (%d bytes)", url, len(body))
    return body, False


def resolve_pdf_with_unpaywall(
    doi: str,
    client: HttpClient | None = None,
) -> str | None:
    """Resolve OA PDF location for a DOI via Unpaywall; None if no OA copy."""
    email = os.environ.get("UNPAYWALL_EMAIL")
    if not email:
        raise PdfError(
            "UNPAYWALL_EMAIL not set; refusing to use Unpaywall AS a search "
            "or with a fake address. Set it to your real email."
        )
    client = client or get_client()
    url = _UNPAYWALL.format(doi=doi)
    body = client.get(url, params={"email": email})
    try:
        import json

        data = json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise PdfError(f"unpaywall response unparseable: {e}") from e

    if not data.get("is_oa"):
        logger.debug("unpaywall: no OA copy for %s", doi)
        return None

    for loc in data.get("oa_locations", []):
        pdf_url = loc.get("url_for_pdf")
        if pdf_url:
            return pdf_url
    return None


def extract_markdown(
    data: bytes,
    *,
    force_fallback: bool = False,
) -> tuple[str, bool]:
    """Extract PDF text to markdown: pymupdf + custom cleaner, with fallback."""
    primary = _extract_with_pymupdf(data)
    if primary and not _is_garbled(primary) and not force_fallback:
        return primary, False
    logger.info("pymupdf output garbled/empty; falling back to pymupdf4llm")
    md = _extract_with_pymupdf4llm(data)
    return md, True


def _extract_with_pymupdf(data: bytes) -> str:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = [page.get_text() for page in doc]
    doc.close()
    return clean_text("\n\n".join(pages))


def _extract_with_pymupdf4llm(data: bytes) -> str:
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return pymupdf4llm.to_markdown(doc)
    finally:
        doc.close()


def _is_garbled(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 200:
        return True
    # very low printable-density -> likely layout garbage
    return len(re.sub(r"\s", "", stripped)) == 0


def clean_text(raw: str) -> str:
    """Custom cleaner: header/footer strip, hyphen rejoin, paragraph merge."""
    lines = _strip_repeated_lines(raw.splitlines())
    lines = _rejoin_hyphens(lines)
    text = _merge_paragraphs(lines)
    # collapse whitespace artifacts left by text extraction
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_repeated_lines(lines: list[str]) -> list[str]:
    """Drop lines that repeat verbatim across pages (headers/footers)."""
    from collections import Counter

    stripped = [ln.strip() for ln in lines]
    counter = Counter(ln for ln in stripped if ln)
    keep = [orig for orig, ln in zip(lines, stripped, strict=True) if counter[ln] < 3]
    return keep


def _rejoin_hyphens(lines: list[str]) -> list[str]:
    """Merge words split by line-break hyphens."""
    out: list[str] = []
    for i, line in enumerate(lines):
        if line.endswith("-") and i + 1 < len(lines):
            out.append(line[:-1].rstrip() + lines[i + 1])
            lines[i + 1] = ""
        else:
            out.append(line)
    return [ln for ln in out if ln != ""]


def _merge_paragraphs(lines: list[str]) -> str:
    """Merge consecutive non-empty lines into paragraphs separated by blanks."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        else:
            if current:
                paragraphs.append(" ".join(current))
                current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)
