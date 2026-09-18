"""Shared browser-impersonating HTTP client built on curl_cffi."""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import os
import threading
import time
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests as crequests

logger = logging.getLogger("artikel_mcp.http")

_POLITE_EMAIL = os.environ.get("ARTIKEL_MCP_EMAIL") or os.environ.get("UNPAYWALL_EMAIL")
_EMAIL_SUFFIX = f" (mailto:{_POLITE_EMAIL})" if _POLITE_EMAIL else ""
UA = (
    f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36{_EMAIL_SUFFIX}"
)
MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_LOCAL_HOST_SUFFIXES = (".local", ".localhost", ".internal")
_SSRF_BYPASS = os.environ.get("ARTIKEL_MCP_ALLOW_PRIVATE_URLS") == "1"


class HttpError(RuntimeError):
    """Raised when an HTTP request fails at the transport or status level."""


def validate_request_url(url: str) -> None:
    """Reject SSRF-prone URLs: non-http(s) schemes, local hosts, private literals.

    Defense-in-depth for user- and adapter-supplied URLs. Set
    ``ARTIKEL_MCP_ALLOW_PRIVATE_URLS=1`` to permit intranet/journal-local endpoints.
    """
    if _SSRF_BYPASS:
        return
    try:
        parsed = urlsplit(url)
    except ValueError as e:
        raise HttpError(f"invalid URL: {url}") from e
    if parsed.scheme not in ("http", "https"):
        raise HttpError(f"refusing non-http(s) URL: {url}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise HttpError(f"refusing URL without a host: {url}")
    if host in ("localhost", "0.0.0.0"):
        raise HttpError(f"refusing local-host URL: {url}")
    if host.endswith(_LOCAL_HOST_SUFFIXES):
        raise HttpError(f"refusing local-network URL: {url}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # hostname; DNS-rebinding risk accepted as residual
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    ):
        raise HttpError(f"refusing private/reserved IP URL: {url}")


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

    def _session_get(self, session, url: str, params, headers, timeout: float):
        """GET with a bounded redirect loop; every hop is validated against SSRF risk."""
        for _ in range(_MAX_REDIRECTS):
            resp = session.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
            if resp.status_code not in _REDIRECT_STATUSES:
                return resp
            loc = resp.headers.get("location")
            if not loc:
                return resp
            next_url = urljoin(url, loc)
            validate_request_url(next_url)
            if resp.status_code in (301, 302, 303):
                params = None  # GET-style redirects drop query params
            url = next_url
            logger.debug("following redirect to %s", url)
        return resp

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
        validate_request_url(url)
        resp = None
        try:
            resp = self._session_get(self._session, url, params, merged, timeout)
        except Exception as e:
            for fb_imp in FALLBACK_IMPERSONATIONS:
                if fb_imp == self._impersonate:
                    continue
                try:
                    if fb_imp not in self._fallback_sessions:
                        self._fallback_sessions[fb_imp] = crequests.Session(
                            impersonate=fb_imp, timeout=self._default_timeout
                        )
                    fb_sess = self._fallback_sessions[fb_imp]
                    fb_resp = self._session_get(fb_sess, url, params, merged, timeout)
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
                    fb_resp = self._session_get(fb_sess, url, params, merged, timeout)
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
                retry_resp = self._session_get(self._session, url, params, merged, timeout)
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

        if len(resp.content) > MAX_RESPONSE_BYTES:
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
