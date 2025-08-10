from __future__ import annotations

from typing import Any, List, Optional

import httpx
from pydantic import Base64Encoder

from .models import DVD, Request as DVDRequest, Response as DVDResponse


# Stack of active DVDs (top of stack is the current active one)
_active_dvds: List[DVD] = []

# Originals for patch/unpatch
_original_client_send = None
_original_async_client_send = None


def _top_dvd() -> Optional[DVD]:
    return _active_dvds[-1] if _active_dvds else None


def _to_dvd_request_from_httpx(request: httpx.Request) -> DVDRequest:
    # httpx.Request provides finalized method, url, and merged headers
    headers = list(request.headers.items())
    return DVDRequest(headers=headers, method=str(request.method).upper(), url=str(request.url))


def _to_httpx_response(dvd_response: DVDResponse, request: httpx.Request | None = None) -> httpx.Response:
    # Construct an httpx.Response from our stored data
    status_code = dvd_response.status
    headers = dvd_response.headers
    body = dvd_response.body or b""
    return httpx.Response(status_code=status_code, headers=headers, content=body, request=request)


def _patch_if_needed():
    global _original_client_send, _original_async_client_send
    if _original_client_send is not None:
        return  # already patched

    _original_client_send = httpx.Client.send
    _original_async_client_send = httpx.AsyncClient.send

    def _patched_client_send(
        self: httpx.Client, request: httpx.Request, *args, **kwargs
    ):  # type: ignore[no-redef]
        dvd = _top_dvd()
        if dvd is None:
            return _original_client_send(self, request, *args, **kwargs)  # type: ignore[misc]

        dvd_req = _to_dvd_request_from_httpx(request)

        # Global passthrough decision. If request is not recordable, always passthrough
        if not dvd.can_record(dvd_req):
            return _original_client_send(self, request, *args, **kwargs)  # type: ignore[misc]

        if dvd.from_file:
            # Attempt to replay using unified API
            dvd_res = dvd.get_request(dvd_req)
            if dvd_res is None:
                raise RuntimeError(
                    "dvd-rw: Request not found in loaded DVD; cannot perform network call in replay mode."
                )
            return _to_httpx_response(dvd_res, request=request)
        else:
            # Perform real request, then record
            try:
                response: httpx.Response = _original_client_send(
                    self, request, *args, **kwargs
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

    async def _patched_async_client_send(
        self: httpx.AsyncClient, request: httpx.Request, *args, **kwargs
    ):  # type: ignore[no-redef]
        dvd = _top_dvd()
        if dvd is None:
            return await _original_async_client_send(
                self, request, *args, **kwargs
            )  # type: ignore[misc]

        dvd_req = _to_dvd_request_from_httpx(request)

        # Global passthrough decision. If request is not recordable, always passthrough
        if not dvd.can_record(dvd_req):
            return await _original_async_client_send(
                self, request, *args, **kwargs
            )  # type: ignore[misc]

        if dvd.from_file:
            dvd_res = dvd.get_request(dvd_req)
            if dvd_res is None:
                raise RuntimeError(
                    "dvd-rw: Request not found in loaded DVD; cannot perform network call in replay mode."
                )
            return _to_httpx_response(dvd_res, request=request)
        else:
            try:
                response: httpx.Response = await _original_async_client_send(
                    self, request, *args, **kwargs
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
                body=Base64Encoder.encode(response.content),
            )
            dvd.record_request(dvd_req, dvd_res)
            return response

    httpx.Client.send = _patched_client_send  # type: ignore[assignment]
    httpx.AsyncClient.send = _patched_async_client_send  # type: ignore[assignment]


def _unpatch_if_possible():
    global _original_client_send, _original_async_client_send
    if _original_client_send is None:
        return  # nothing to unpatch

    # Restore originals
    httpx.Client.send = _original_client_send  # type: ignore[assignment]
    httpx.AsyncClient.send = _original_async_client_send  # type: ignore[assignment]

    _original_client_send = None
    _original_async_client_send = None


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
