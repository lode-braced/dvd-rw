from __future__ import annotations

from typing import Any, List, Optional

import httpx
from pydantic import Base64Encoder

from .models import DVD, Request as DVDRequest, Response as DVDResponse


# Stack of active DVDs (top of stack is the current active one)
_active_dvds: List[DVD] = []

# Originals for patch/unpatch
_original_client_request = None
_original_async_client_request = None


def _top_dvd() -> Optional[DVD]:
    return _active_dvds[-1] if _active_dvds else None


def _to_dvd_request(method: str, url: str, headers: Any | None) -> DVDRequest:
    # httpx accepts headers as dict, list of tuples, or Headers. Normalize to list[tuple[str, str]]
    header_items: list[tuple[str, str]]
    if headers is None:
        header_items = []
    elif isinstance(headers, httpx.Headers):
        header_items = list(headers.items())
    elif isinstance(headers, dict):
        # Preserve insertion order as given; httpx normalizes case but for matching we suggest avoiding headers in match_on
        header_items = [(str(k), str(v)) for k, v in headers.items()]
    else:
        # Assume iterable of pairs
        header_items = [(str(k), str(v)) for k, v in headers]

    return DVDRequest(headers=header_items, method=str(method).upper(), url=str(url))


def _to_httpx_response(dvd_response: DVDResponse) -> httpx.Response:
    # Construct an httpx.Response from our stored data
    status_code = dvd_response.status
    headers = dvd_response.headers
    body = dvd_response.body or b""
    return httpx.Response(status_code=status_code, headers=headers, content=body)


def _patch_if_needed():
    global _original_client_request, _original_async_client_request
    if _original_client_request is not None:
        return  # already patched

    _original_client_request = httpx.Client.request
    _original_async_client_request = httpx.AsyncClient.request

    def _patched_client_request(
        self: httpx.Client, method: str, url: str, *args, **kwargs
    ):  # type: ignore[no-redef]
        dvd = _top_dvd()
        if dvd is None:
            return _original_client_request(self, method, url, *args, **kwargs)  # type: ignore[misc]

        # Extract headers from kwargs without mutating the call
        headers = kwargs.get("headers")
        dvd_req = _to_dvd_request(method, url, headers)

        # First: global passthrough decision. If request is not recordable, always passthrough
        if not dvd.can_record(dvd_req):
            return _original_client_request(self, method, url, *args, **kwargs)  # type: ignore[misc]

        if dvd.from_file:
            # Attempt to replay using unified API
            dvd_res = dvd.get_request(dvd_req)
            if dvd_res is None:
                raise RuntimeError(
                    "dvd-rw: Request not found in loaded DVD; cannot perform network call in replay mode."
                )
            return _to_httpx_response(dvd_res)
        else:
            # Perform real request, then record
            try:
                response: httpx.Response = _original_client_request(
                    self, method, url, *args, **kwargs
                )  # type: ignore[misc]
            except Exception as exc:
                dvd.record_request(dvd_req, exc)
                raise
            # Ensure content is loaded for recording; no-op if already loaded
            try:
                _ = response.content
            except Exception:
                pass
            dvd_res = DVDResponse(
                status=response.status_code,
                headers=list(response.headers.items()),
                body=Base64Encoder.encode(response.content),
            )
            dvd.record_request(dvd_req, dvd_res)
            return response

    async def _patched_async_client_request(
        self: httpx.AsyncClient, method: str, url: str, *args, **kwargs
    ):  # type: ignore[no-redef]
        dvd = _top_dvd()
        if dvd is None:
            return await _original_async_client_request(
                self, method, url, *args, **kwargs
            )  # type: ignore[misc]

        headers = kwargs.get("headers")
        dvd_req = _to_dvd_request(method, url, headers)

        # First: global passthrough decision. If request is not recordable, always passthrough
        if not dvd.can_record(dvd_req):
            return await _original_async_client_request(
                self, method, url, *args, **kwargs
            )  # type: ignore[misc]

        if dvd.from_file:
            dvd_res = dvd.get_request(dvd_req)
            if dvd_res is None:
                raise RuntimeError(
                    "dvd-rw: Request not found in loaded DVD; cannot perform network call in replay mode."
                )
            return _to_httpx_response(dvd_res)
        else:
            try:
                response: httpx.Response = await _original_async_client_request(
                    self, method, url, *args, **kwargs
                )  # type: ignore[misc]
            except Exception as exc:
                dvd.record_request(dvd_req, exc)
                raise
            # Ensure content is loaded
            try:
                await response.aread()
            except Exception:
                pass
            dvd_res = DVDResponse(
                status=response.status_code,
                headers=list(response.headers.items()),
                body=response.content,
            )
            dvd.record_request(dvd_req, dvd_res)
            return response

    httpx.Client.request = _patched_client_request  # type: ignore[assignment]
    httpx.AsyncClient.request = _patched_async_client_request  # type: ignore[assignment]


def _unpatch_if_possible():
    global _original_client_request, _original_async_client_request
    if _original_client_request is None:
        return  # nothing to unpatch

    # Restore originals
    httpx.Client.request = _original_client_request  # type: ignore[assignment]
    httpx.AsyncClient.request = _original_async_client_request  # type: ignore[assignment]

    _original_client_request = None
    _original_async_client_request = None


def push_dvd(dvd: DVD):
    """Activate a DVD for patching stack; install patches on first push."""
    _patch_if_needed()
    _active_dvds.append(dvd)


def pop_dvd(expected: DVD | None = None):
    """Deactivate the top DVD; unpatch if stack becomes empty.

    If expected is provided and is not the current top, remove it wherever it is
    in the stack to maintain safety, but prefer LIFO usage.
    """
    if not _active_dvds:
        return

    if expected is None or (_active_dvds and _active_dvds[-1] is expected):
        _active_dvds.pop()
    else:
        # Remove first occurrence for robustness
        for i, d in enumerate(reversed(_active_dvds), 1):
            if d is expected:
                del _active_dvds[-i]
                break
        else:
            # expected not found; pop the top to keep moving
            _active_dvds.pop()

    if not _active_dvds:
        _unpatch_if_possible()
