"""Google Scholar HTML adapter built with curl_cffi.

Includes anti-aggression pacing, persistent browser sessions,
CAPTCHA/bot detection with circuit breaking, and robust HTML extraction.
"""

from __future__ import annotations

import contextlib
import html
import logging
import os
import random
import re
import threading
import time
from typing import Any

from curl_cffi import requests as crequests

from artikel_mcp.models import PaperRecord
from artikel_mcp.sources.base import AdapterError, SourceAdapter

logger = logging.getLogger("artikel_mcp.sources.scholar")

SCHOLAR_URL = "https://scholar.google.com/scholar"

# Regular expressions for Google Scholar HTML parsing
_RESULT_ITEM_RE = re.compile(
    r'<div\s+class="gs_r\s+gs_or\s+gs_scl"[^>]*>(.*?)</div>\s*</div>\s*</div>',
    re.DOTALL,
)
_CID_RE = re.compile(r'data-cid="([^"]+)"')
_TITLE_ANCHOR_RE = re.compile(r'<h3\s+class="gs_rt"[^>]*>(.*?)</h3>', re.DOTALL)
_HREF_RE = re.compile(r'href="([^"]+)"')
_AUTHOR_DIV_RE = re.compile(r'<div\s+class="gs_a"[^>]*>(.*?)</div>', re.DOTALL)
_SNIPPET_DIV_RE = re.compile(r'<div\s+class="gs_rs"[^>]*>(.*?)</div>', re.DOTALL)
_PDF_DIV_RE = re.compile(r'<div\s+class="gs_or_ggsm"[^>]*>(.*?)</div>', re.DOTALL)
_CITES_RE = re.compile(
    r'<a\s+href="([^"]*cites=(\d+)[^"]*)"[^>]*>Cited by\s+(\d+)</a>', re.IGNORECASE
)
_RELATED_RE = re.compile(r'<a\s+href="([^"]*related=([^"&]+)[^"]*)"[^>]*>', re.IGNORECASE)
_VERSIONS_RE = re.compile(
    r'<a\s+href="([^"]*cluster=(\d+)[^"]*)"[^>]*>All\s+(\d+)\s+versions</a>', re.IGNORECASE
)
_YEAR_RE = re.compile(r"\b(19\d\d|20\d\d)\b")
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_LABEL_STRIP_RE = re.compile(r"\[(PDF|HTML|BOOK|CITATION|B|C)\]\s*", re.IGNORECASE)
_DOI_EXTRACT_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.IGNORECASE)


class ScholarRateLimiter:
    """Process-wide rate limiter and circuit breaker for Google Scholar requests."""

    def __init__(
        self,
        min_delay: float = 3.5,
        max_jitter: float = 2.5,
        cooldown: float = 60.0,
    ) -> None:
        self._min_delay = min_delay
        self._max_jitter = max_jitter
        self._cooldown = cooldown
        self._lock = threading.Lock()
        self._last_request_time = 0.0
        self._cooldown_until = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                remaining = int(self._cooldown_until - now)
                raise AdapterError(
                    f"Google Scholar rate-limiting / CAPTCHA cooldown active "
                    f"({remaining}s remaining). Configure GOOGLE_SCHOLAR_COOKIE "
                    f"or GOOGLE_SCHOLAR_PROXY to bypass."
                )

            elapsed = now - self._last_request_time
            delay = self._min_delay + random.uniform(0, self._max_jitter)
            if elapsed < delay and self._last_request_time > 0:
                sleep_time = delay - elapsed
                logger.debug("Google Scholar pacing: sleeping %.2fs", sleep_time)
                time.sleep(sleep_time)
            self._last_request_time = time.monotonic()

    def trip_cooldown(self, seconds: float | None = None) -> None:
        with self._lock:
            dur = seconds if seconds is not None else self._cooldown
            self._cooldown_until = time.monotonic() + dur
            logger.warning(
                "Google Scholar circuit breaker tripped; cooling down for %.1fs",
                dur,
            )

    def reset(self) -> None:
        with self._lock:
            self._cooldown_until = 0.0
            self._last_request_time = 0.0


_GLOBAL_LIMITER = ScholarRateLimiter(
    min_delay=float(os.getenv("GOOGLE_SCHOLAR_DELAY", "3.5")),
    max_jitter=2.0,
)

_session_lock = threading.Lock()
_shared_session: crequests.Session | None = None


def _get_scholar_session() -> crequests.Session:
    global _shared_session
    with _session_lock:
        if _shared_session is None:
            impersonate = os.getenv("GOOGLE_SCHOLAR_IMPERSONATE", "chrome124")

            proxy = os.getenv("GOOGLE_SCHOLAR_PROXY") or os.getenv("HTTPS_PROXY")
            if not proxy and os.getenv("GOOGLE_SCHOLAR_USE_CUSTOM_PROXY", "0") == "1":
                from artikel_mcp.proxy_engine import get_or_start_proxy_engine

                get_or_start_proxy_engine()
                proxy = "socks5h://127.0.0.1:10808"

            # Enforce socks5h:// over socks5:// to guarantee zero DNS leaks
            if proxy and proxy.startswith("socks5://"):
                proxy = "socks5h://" + proxy[len("socks5://") :]

            proxies = {"http": proxy, "https": proxy} if proxy else None
            _shared_session = crequests.Session(
                impersonate=impersonate,
                timeout=25.0,
                proxies=proxies,
            )
            custom_cookie = os.getenv("GOOGLE_SCHOLAR_COOKIE")
            if custom_cookie:
                _shared_session.headers["Cookie"] = custom_cookie
        return _shared_session


def _reset_scholar_session() -> None:
    """Close and reset the shared curl_cffi session to force a fresh connection."""
    global _shared_session
    with _session_lock:
        if _shared_session is not None:
            with contextlib.suppress(Exception):
                _shared_session.close()
            _shared_session = None


def _is_captcha_or_blocked(status_code: int, url: str, text: str) -> bool:
    if status_code in (429, 403):
        return True
    if "/sorry/index" in url:
        return True
    lower = text.lower()
    if "onload=\"e=document.getElementById('captcha')" in text:
        return True
    if 'class="g-recaptcha"' in text or "recaptcha/enterprise" in text:
        return True
    return bool("detected unusual traffic from your computer network" in lower)


def parse_scholar_html(html_text: str) -> list[PaperRecord]:
    """Parse Google Scholar search result page HTML into PaperRecord list."""
    records: list[PaperRecord] = []

    # Find result blocks and retain opening tag containing data-cid
    start_positions = [
        (m.start(), m.group(0))
        for m in re.finditer(
            r'<div\s+[^>]*class="[^"]*gs_r\s+gs_or\s+gs_scl[^"]*"[^>]*>', html_text
        )
    ]
    for idx, (start, opening_tag) in enumerate(start_positions):
        end = start_positions[idx + 1][0] if idx + 1 < len(start_positions) else None
        chunk = html_text[start:end]

        # Extract cluster ID (data-cid)
        cid_match = _CID_RE.search(opening_tag) or _CID_RE.search(chunk)
        cid = cid_match.group(1) if cid_match else ""

        # Title & URL
        title_match = _TITLE_ANCHOR_RE.search(chunk)
        if not title_match:
            continue
        raw_title_block = title_match.group(1)

        href_match = _HREF_RE.search(raw_title_block)
        article_url = href_match.group(1) if href_match else None

        # Clean title text: strip html tags & [PDF]/[HTML] badges
        title_text = _TAG_STRIP_RE.sub("", raw_title_block)
        title_text = _LABEL_STRIP_RE.sub("", title_text)
        title_text = " ".join(html.unescape(title_text).split()).strip()
        if not title_text:
            continue

        # Direct PDF link from right-hand side preview
        pdf_url = None
        pdf_match = _PDF_DIV_RE.search(chunk)
        if pdf_match:
            pdf_href = _HREF_RE.search(pdf_match.group(1))
            if pdf_href:
                pdf_url = html.unescape(pdf_href.group(1))

        # Authors & Publication from gs_a
        authors: list[str] = []
        publication = "Google Scholar"
        year: int | None = None

        author_match = _AUTHOR_DIV_RE.search(chunk)
        if author_match:
            raw_meta = _TAG_STRIP_RE.sub("", author_match.group(1))
            raw_meta = html.unescape(raw_meta).strip()
            # Google Scholar separates authors, venue/year, and publisher by " - "
            segments = [s.strip() for s in raw_meta.split(" - ") if s.strip()]
            if segments:
                # Authors in first segment
                author_names = [a.strip() for a in segments[0].split(",") if a.strip()]
                # Exclude ellipsis or empty names
                authors = [a for a in author_names if a != "…"]

                # Check for year in any segment
                for seg in segments:
                    ym = _YEAR_RE.search(seg)
                    if ym:
                        year = int(ym.group(1))
                        break

                if len(segments) >= 3:
                    publication = f"{segments[1]} ({segments[2]})"
                elif len(segments) == 2:
                    publication = segments[1]

        # Abstract / Snippet from gs_rs
        abstract = None
        snippet_match = _SNIPPET_DIV_RE.search(chunk)
        if snippet_match:
            clean_snippet = _TAG_STRIP_RE.sub("", snippet_match.group(1))
            clean_snippet = " ".join(html.unescape(clean_snippet).split()).strip()
            abstract = clean_snippet or None

        # Metrics & links from gs_fl
        extra: dict[str, Any] = {}
        if cid:
            extra["cluster_id"] = cid

        cites_match = _CITES_RE.search(chunk)
        if cites_match:
            extra["cites_url"] = "https://scholar.google.com" + cites_match.group(1)
            extra["citations_count"] = int(cites_match.group(3))

        related_match = _RELATED_RE.search(chunk)
        if related_match:
            extra["related_url"] = "https://scholar.google.com" + related_match.group(1)

        versions_match = _VERSIONS_RE.search(chunk)
        if versions_match:
            extra["versions_count"] = int(versions_match.group(3))

        # Check for DOI in URL or snippet
        doi = None
        if article_url:
            dm = _DOI_EXTRACT_RE.search(article_url)
            if dm:
                doi = dm.group(1)

        source_id = cid or (doi or str(abs(hash(title_text))))

        record = PaperRecord(
            source="scholar",
            source_id=source_id,
            title=title_text,
            authors=authors,
            doi=doi,
            url=article_url
            or (f"https://scholar.google.com/scholar?cluster={cid}" if cid else None),
            publication=publication,
            abstract=abstract,
            year=year,
            pdf_url=pdf_url,
            extra=extra,
        )
        records.append(record)

    return records


class ScholarAdapter(SourceAdapter):
    """Source adapter for Google Scholar using curl_cffi with anti-aggression safeguards."""

    name = "scholar"

    def __init__(
        self,
        client: Any | None = None,
        rate_limiter: ScholarRateLimiter | None = None,
    ) -> None:
        self._custom_client = client
        self._limiter = rate_limiter or _GLOBAL_LIMITER

    def _fetch(self, url: str, params: dict[str, str]) -> tuple[int, str, str]:
        """Fetch URL with curl_cffi, enforcing rate limiting and session reuse."""
        self._limiter.acquire()

        if self._custom_client is not None:
            # Custom / mock client for unit testing
            if hasattr(self._custom_client, "get_response"):
                resp = self._custom_client.get_response(url, params=params)
                return resp.status_code, getattr(resp, "url", url), resp.text
            raw = self._custom_client.get(url, params=params)
            return 200, url, raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

        session = _get_scholar_session()
        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

        try:
            resp = session.get(url, params=params, headers=headers)
        except Exception as e:
            raise AdapterError(f"Google Scholar network request failed: {e}") from e

        return resp.status_code, resp.url, resp.text

    def search(self, query: str, limit: int = 10) -> list[PaperRecord]:
        clean_q = query.strip()
        if not clean_q:
            return []

        # Google Scholar serves 10 results per page.
        # Cap pagination to avoid triggering bot detection (max 30 results = 3 pages).
        bounded_limit = max(1, min(limit, 30))
        max_pages = (bounded_limit + 9) // 10

        max_retries = int(os.getenv("GOOGLE_SCHOLAR_MAX_RETRIES", "3"))
        all_records: list[PaperRecord] = []

        for page in range(max_pages):
            start = page * 10
            params = {
                "hl": "en",
                "as_sdt": "0,5",
                "q": clean_q,
            }
            if start > 0:
                params["start"] = str(start)

            page_records = None
            last_error = None

            for attempt in range(max_retries):
                try:
                    status_code, final_url, html_text = self._fetch(SCHOLAR_URL, params=params)

                    if _is_captcha_or_blocked(status_code, final_url, html_text):
                        logger.warning(
                            "Google Scholar CAPTCHA / 429 encountered (attempt %d/%d). "
                            "Flagging active proxy for 24h cooldown and auto-rotating...",
                            attempt + 1,
                            max_retries,
                        )
                        from artikel_mcp.proxy_engine import mark_active_proxy_captcha

                        mark_active_proxy_captcha(hours=24.0)
                        _reset_scholar_session()

                        if attempt < max_retries - 1:
                            time.sleep(0.5)
                            continue

                        self._limiter.trip_cooldown()
                        raise AdapterError(
                            "Google Scholar anti-bot CAPTCHA or rate-limit triggered "
                            "on all attempts. Proxies flagged for 24h cooldown."
                        )

                    page_records = parse_scholar_html(html_text)
                    break
                except AdapterError:
                    raise
                except Exception as e:
                    last_error = e
                    logger.warning(
                        "Scholar fetch error on attempt %d/%d: %s. Rotating proxy...",
                        attempt + 1,
                        max_retries,
                        e,
                    )
                    from artikel_mcp.proxy_engine import mark_active_proxy_captcha

                    mark_active_proxy_captcha(hours=1.0)
                    _reset_scholar_session()
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        continue

            if page_records is None:
                if last_error:
                    raise AdapterError(
                        f"Scholar query failed after {max_retries} attempts: {last_error}"
                    ) from last_error
                break

            if not page_records:
                break

            all_records.extend(page_records)
            if len(all_records) >= bounded_limit:
                break

        return all_records[:bounded_limit]

    def smoke(self, query: str = "status hukum deepfake di indonesia") -> list[PaperRecord]:
        recs = self.search(query, limit=5)
        logger.info("Google Scholar smoke test returned %d records", len(recs))
        return recs
