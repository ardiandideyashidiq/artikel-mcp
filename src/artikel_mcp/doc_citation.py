"""Document citation manager: CRUD citations within a document and auto-generate bibliographies."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from artikel_mcp.cache import PaperCache
from artikel_mcp.citation import (
    format_bibliography,
    format_in_text,
    parse_author_name,
)
from artikel_mcp.models import PaperRecord

logger = logging.getLogger("artikel_mcp.doc_citation")

_PANDOC_CITE_RE = re.compile(r"\[@([a-zA-Z0-9_.:/\\-]+)(?:,[^\]]*)?\]")
_INLINE_CITE_RE = re.compile(r"(?<!\w)@([a-zA-Z0-9_.:/\\-]+)")
_COMMENT_CITE_RE = re.compile(r"<!--\s*cite:\s*([^\s>]+)\s*-->")
_DOI_EXPLICIT_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
_BIB_HEADER_RE = re.compile(
    r"^(#{1,3}\s+(?:references|daftar pustaka|bibliography|works cited|literature cited))\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_citekey(record: PaperRecord) -> str:
    """Generate a clean, stable citation key (e.g. noerman2024)."""
    if record.authors:
        last = parse_author_name(record.authors[0]).last
        clean_last = re.sub(r"\W+", "", last).lower()
    else:
        clean_last = "item"

    year = str(record.year) if record.year else "nodate"
    return f"{clean_last}{year}"


def resolve_cached_paper(cache: PaperCache, key: str) -> PaperRecord | None:
    """Find paper in cache by DOI, dedup_key, URL, or generated citekey."""
    clean_k = key.strip()
    rec = cache.get_by_key(clean_k)
    if rec:
        return rec
    lower_k = clean_k.lower()
    for p in cache.list_all(limit=1000):
        if extract_citekey(p).lower() == lower_k:
            return p
        if p.doi and p.doi.strip().lower() == lower_k:
            return p
        if p.dedup_key().lower() == lower_k:
            return p
    return None


def scan_file_citations(file_path: str | Path, cache: PaperCache) -> dict:
    """Scan a text, markdown, or LaTeX file for cited paper keys/DOIs."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    content = path.read_text(encoding="utf-8")

    # Exclude bibliography section from citation scanning if present
    content_to_scan = content
    m_bib = _BIB_HEADER_RE.search(content)
    if m_bib:
        content_to_scan = content[: m_bib.start()]

    raw_candidates: set[str] = set()

    # 1. Pandoc citations [@key]
    for m in _PANDOC_CITE_RE.finditer(content_to_scan):
        raw_candidates.add(m.group(1).strip())

    # 2. Inline @key
    for m in _INLINE_CITE_RE.finditer(content_to_scan):
        cand = m.group(1).strip()
        # Avoid common email or handle patterns
        if not cand.endswith(".com") and not cand.endswith(".org"):
            raw_candidates.add(cand)

    # 3. HTML comments <!-- cite: key -->
    for m in _COMMENT_CITE_RE.finditer(content_to_scan):
        raw_candidates.add(m.group(1).strip())

    # 4. Explicit DOIs in text
    for m in _DOI_EXPLICIT_RE.finditer(content_to_scan):
        raw_candidates.add(m.group(1).strip())

    resolved_records: list[PaperRecord] = []
    unresolved_keys: list[str] = []
    seen_dedup_keys: set[str] = set()

    for raw_k in raw_candidates:
        rec = resolve_cached_paper(cache, raw_k)
        if rec:
            dk = rec.dedup_key()
            if dk not in seen_dedup_keys:
                seen_dedup_keys.add(dk)
                resolved_records.append(rec)
        else:
            unresolved_keys.append(raw_k)

    return {
        "file_path": str(path),
        "total_citations_found": len(raw_candidates),
        "raw_citations": sorted(raw_candidates),
        "resolved_count": len(resolved_records),
        "resolved_papers": [
            {
                "citekey": extract_citekey(r),
                "doi": r.doi,
                "title": r.title,
                "authors": r.authors,
                "year": r.year,
                "publication": r.publication,
            }
            for r in resolved_records
        ],
        "unresolved_keys": sorted(unresolved_keys),
    }


def insert_citation_in_file(
    file_path: str | Path,
    doi_or_key: str,
    cache: PaperCache,
    *,
    line_number: int | None = None,
    marker_format: str = "pandoc",
    style: str = "apa7",
    narrative: bool = False,
    auto_sync: bool = True,
) -> dict:
    """Insert citation marker or rendered citation into a file."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    rec = resolve_cached_paper(cache, doi_or_key)
    if not rec:
        raise ValueError(f"Paper '{doi_or_key}' not found in cache")

    cite_key = extract_citekey(rec)
    content = path.read_text(encoding="utf-8")

    # Generate citation token
    if marker_format == "pandoc":
        token = f"@{cite_key}" if narrative else f"[@{cite_key}]"
    elif marker_format == "rendered":
        token = format_in_text(rec, style=style, narrative=narrative)
    elif marker_format == "comment":
        token = f"<!-- cite: {rec.doi or cite_key} -->"
    else:
        token = f"[@{cite_key}]"

    lines = content.splitlines()

    # Determine insertion position
    m_bib = _BIB_HEADER_RE.search(content)
    max_line = len(lines)
    if m_bib:
        bib_start_char = m_bib.start()
        max_line = content[:bib_start_char].count("\n")

    if line_number is None or line_number > max_line:
        # Append before bibliography section or at end of body
        if m_bib:
            body_part = content[: m_bib.start()].rstrip() + f" {token}\n\n"
            content = body_part + content[m_bib.start() :]
        else:
            content = content.rstrip() + f" {token}\n"
    else:
        target_idx = max(0, line_number - 1)
        if target_idx < len(lines):
            lines[target_idx] = f"{lines[target_idx]} {token}".strip()
            content = "\n".join(lines)
        else:
            lines.append(token)
            content = "\n".join(lines)

    path.write_text(content, encoding="utf-8")
    logger.info("inserted citation '%s' into %s", token, path)

    bib_result = None
    if auto_sync:
        bib_result = sync_file_bibliography(path, cache, style=style)

    return {
        "success": True,
        "file_path": str(path),
        "inserted_token": token,
        "citekey": cite_key,
        "paper_title": rec.title,
        "auto_synced": bool(bib_result and bib_result["success"]),
    }


def remove_citation_from_file(
    file_path: str | Path,
    doi_or_key: str,
    cache: PaperCache,
    *,
    sync_bib: bool = True,
    style: str = "apa7",
) -> dict:
    """Remove all citation tokens matching a paper from a document."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    rec = resolve_cached_paper(cache, doi_or_key)
    cite_key = extract_citekey(rec) if rec else doi_or_key.strip()
    doi_clean = rec.doi if rec and rec.doi else doi_or_key.strip()

    content = path.read_text(encoding="utf-8")

    patterns = [
        re.compile(rf"\[@{re.escape(cite_key)}(?:,[^\]]*)?\]"),
        re.compile(rf"@\b{re.escape(cite_key)}\b"),
        re.compile(rf"<!--\s*cite:\s*{re.escape(cite_key)}\s*-->"),
        re.compile(rf"<!--\s*cite:\s*{re.escape(doi_clean)}\s*-->"),
    ]
    if rec:
        rendered_parens = format_in_text(rec, style=style, narrative=False)
        rendered_narrative = format_in_text(rec, style=style, narrative=True)
        patterns.append(re.compile(re.escape(rendered_parens)))
        patterns.append(re.compile(re.escape(rendered_narrative)))

    removed_count = 0
    new_content = content
    for pat in patterns:
        matches = pat.findall(new_content)
        removed_count += len(matches)
        new_content = pat.sub("", new_content)

    # Clean up double spaces created by deletion
    new_content = re.sub(r"[ \t]{2,}", " ", new_content)
    path.write_text(new_content, encoding="utf-8")

    bib_result = None
    if sync_bib:
        bib_result = sync_file_bibliography(path, cache, style=style)

    return {
        "success": True,
        "file_path": str(path),
        "removed_tokens_count": removed_count,
        "citekey": cite_key,
        "bib_synced": bool(bib_result and bib_result["success"]),
    }


def sync_file_bibliography(
    file_path: str | Path,
    cache: PaperCache,
    *,
    style: str = "apa7",
    section_heading: str = "## References",
    companion_bib: bool = True,
) -> dict:
    """Scan file citations and regenerate references section and .bib companion."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    scan = scan_file_citations(path, cache)
    records: list[PaperRecord] = []
    for p in scan["resolved_papers"]:
        r = resolve_cached_paper(cache, p["doi"] or p["citekey"])
        if r:
            records.append(r)

    content = path.read_text(encoding="utf-8")

    # Format bibliography text
    if records:
        bib_text = format_bibliography(records, style=style)
    else:
        bib_text = "*No cited papers found in document.*"

    # Replace existing References section or append
    m_bib = _BIB_HEADER_RE.search(content)
    if m_bib:
        # Keep heading string found or use section_heading
        heading_found = m_bib.group(1)
        body = content[: m_bib.start()].rstrip()
        new_content = f"{body}\n\n{heading_found}\n\n{bib_text}\n"
    else:
        new_content = f"{content.rstrip()}\n\n{section_heading}\n\n{bib_text}\n"

    path.write_text(new_content, encoding="utf-8")
    logger.info("synced bibliography for %s (%d papers, style=%s)", path, len(records), style)

    companion_bib_path = None
    if companion_bib and records:
        bib_path = path.parent / f"{path.stem}.bib"
        bibtex_content = format_bibliography(records, style="bibtex")
        bib_path.write_text(bibtex_content, encoding="utf-8")
        companion_bib_path = str(bib_path)
        logger.info("wrote companion BibTeX file to %s", bib_path)

    return {
        "success": True,
        "file_path": str(path),
        "style": style,
        "cited_count": len(records),
        "unresolved_count": len(scan["unresolved_keys"]),
        "unresolved_keys": scan["unresolved_keys"],
        "companion_bib": companion_bib_path,
        "bibliography": bib_text,
    }
