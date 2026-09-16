"""Shared browser-impersonating HTTP client built on curl_cffi."""

from __future__ import annotations

import logging

from curl_cffi import requests as crequests

logger = logging.getLogger("artikel_mcp.http")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class HttpError(RuntimeError):
    """Raised when an HTTP request fails at the transport or status level."""


def _log_error(url: str, resp) -> None:
    logger.warning(
        "HTTP %s GET %s (%s)",
        resp.status_code,
        url,
        resp.headers.get("content-type", ""),
    )
    try:
        snippet = resp.text[:300]
    except Exception:
        snippet = ""
    logger.debug("error body: %s", snippet)


class HttpClient:
    """Thin wrapper around a curl_cffi session with browser impersonation."""

    def __init__(self, impersonate: str = "chrome124", timeout: float = 20.0):
        self._session = crequests.Session(impersonate=impersonate, timeout=timeout)
        self._default_timeout = timeout

    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> bytes:
        return self._request(url, params=params, headers=headers, timeout=timeout)

    def get_response(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ):
        """Return the raw response object for callers that need it."""
        return self._request(url, params=params, headers=headers, timeout=timeout, raw=True)

    def _request(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        raw: bool = False,
    ):
        merged = {"User-Agent": UA}
        if headers:
            merged.update(headers)
        timeout = timeout if timeout is not None else self._default_timeout
        logger.debug("GET %s params=%s timeout=%s", url, params or {}, timeout)
        try:
            resp = self._session.get(url, params=params, headers=merged, timeout=timeout)
        except crequests.RequestException as e:
            raise HttpError(f"GET {url} failed: {e}") from e
        if resp.status_code >= 400:
            _log_error(url, resp)
            raise HttpError(f"GET {url} -> HTTP {resp.status_code}")
        return resp if raw else resp.content


_default: HttpClient | None = None


def get_client() -> HttpClient:
    global _default
    if _default is None:
        _default = HttpClient()
    return _default
