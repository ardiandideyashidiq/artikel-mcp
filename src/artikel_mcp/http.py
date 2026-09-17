"""Shared browser-impersonating HTTP client built on curl_cffi."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time

from curl_cffi import requests as crequests

logger = logging.getLogger("artikel_mcp.http")

_POLITE_EMAIL = os.environ.get("ARTIKEL_MCP_EMAIL") or os.environ.get("UNPAYWALL_EMAIL")
_EMAIL_SUFFIX = f" (mailto:{_POLITE_EMAIL})" if _POLITE_EMAIL else ""
UA = (
    f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36{_EMAIL_SUFFIX}"
)
MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB


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


FALLBACK_IMPERSONATIONS = ("safari15_5", "firefox133")


class HttpClient:
    """Thin wrapper around a curl_cffi session with browser impersonation and WAF fallback."""

    def __init__(self, impersonate: str = "chrome124", timeout: float = 20.0):
        self._impersonate = impersonate
        self._session = crequests.Session(impersonate=impersonate, timeout=timeout)
        self._default_timeout = timeout
        self._fallback_sessions: dict[str, crequests.Session] = {}

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
        resp = None
        try:
            resp = self._session.get(url, params=params, headers=merged, timeout=timeout)
        except (crequests.RequestsError, Exception) as e:
            for fb_imp in FALLBACK_IMPERSONATIONS:
                if fb_imp == self._impersonate:
                    continue
                try:
                    if fb_imp not in self._fallback_sessions:
                        self._fallback_sessions[fb_imp] = crequests.Session(
                            impersonate=fb_imp, timeout=self._default_timeout
                        )
                    fb_sess = self._fallback_sessions[fb_imp]
                    fb_headers = headers or None
                    fb_resp = fb_sess.get(url, params=params, headers=fb_headers, timeout=timeout)
                    if fb_resp.status_code < 400:
                        logger.info(
                            "Transport error on %s bypassed using impersonation=%s (status=%d)",
                            url,
                            fb_imp,
                            fb_resp.status_code,
                        )
                        resp = fb_resp
                        break
                except Exception:
                    pass
            if resp is None:
                raise HttpError(f"GET {url} failed: {e}") from e

        # If blocked by WAF (e.g. HCDN anti-bot challenge on Chromium TLS fingerprints),
        # retry with alternate browser impersonations (Safari, Firefox).
        if resp.status_code == 403:
            for fb_imp in FALLBACK_IMPERSONATIONS:
                if fb_imp == self._impersonate:
                    continue
                try:
                    if fb_imp not in self._fallback_sessions:
                        self._fallback_sessions[fb_imp] = crequests.Session(
                            impersonate=fb_imp, timeout=self._default_timeout
                        )
                    fb_sess = self._fallback_sessions[fb_imp]
                    fb_headers = headers or None
                    fb_resp = fb_sess.get(url, params=params, headers=fb_headers, timeout=timeout)
                    if fb_resp.status_code < 400:
                        logger.info(
                            "HTTP 403 bypassed on %s using impersonation=%s (status=%d)",
                            url,
                            fb_imp,
                            fb_resp.status_code,
                        )
                        resp = fb_resp
                        break
                except Exception as fb_err:
                    logger.debug("fallback impersonation %s failed for %s: %s", fb_imp, url, fb_err)

        # Transient 429 / 5xx retry with backoff
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = 1.0
            if "retry-after" in resp.headers:
                with contextlib.suppress(ValueError):
                    retry_after = min(float(resp.headers["retry-after"]), 5.0)
            logger.info(
                "HTTP %d on %s; retrying after %.1fs backoff",
                resp.status_code,
                url,
                retry_after,
            )
            time.sleep(retry_after)
            try:
                retry_resp = self._session.get(url, params=params, headers=merged, timeout=timeout)
                if retry_resp.status_code < 400:
                    resp = retry_resp
            except Exception:
                pass

        if resp.status_code >= 400:
            _log_error(url, resp)
            raise HttpError(f"GET {url} -> HTTP {resp.status_code}")

        # Check response size cap
        cl = resp.headers.get("content-length")
        if cl:
            try:
                if int(cl) > MAX_RESPONSE_BYTES:
                    raise HttpError(f"GET {url} response size exceeds 50MB limit ({cl} bytes)")
            except ValueError:
                pass

        if not raw and len(resp.content) > MAX_RESPONSE_BYTES:
            raise HttpError(f"GET {url} response content exceeds 50MB limit")

        return resp if raw else resp.content


_local = threading.local()


def get_client() -> HttpClient:
    """Return a per-thread HttpClient (curl_cffi Session is not thread-safe)."""
    client = getattr(_local, "default", None)
    if client is None:
        client = HttpClient()
        _local.default = client
    return client
