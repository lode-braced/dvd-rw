import httpx
import pytest

from dvd_rw.loader import DVDLoader
from dvd_rw.models import Matcher, RequestExceptionInfo


BASE_URL = "https://example.com"


def make_url(path: str) -> str:
    return f"{BASE_URL}{path}"


def test_record_and_replay_exception_sync(tmp_path):
    file_path = tmp_path / "dvd_exc1.json"

    # Transport that raises an exception
    def raise_connect_error(request: httpx.Request):
        raise httpx.ConnectError("conn failed", request=request)

    transport_raises = httpx.MockTransport(raise_connect_error)

    # Record mode: perform request and record exception
    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ) as dvd:
        client = httpx.Client(transport=transport_raises)
        with pytest.raises(httpx.ConnectError) as ei:
            client.get(make_url("/err"))
        assert "conn failed" in str(ei.value)
        # Ensure exception was recorded in unified recorded_requests
        assert len(dvd.recorded_requests) == 1
        req0, val0 = dvd.recorded_requests[0]
        assert isinstance(val0, RequestExceptionInfo)
        assert req0.url == make_url("/err")

    # Replay mode: ensure we re-raise the stored exception and do not hit network
    def fail_handler(request):  # pragma: no cover - should not be called
        raise AssertionError("Network should not be hit during replay")

    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ):
        client = httpx.Client(transport=httpx.MockTransport(fail_handler))
        with pytest.raises(httpx.ConnectError) as ei2:
            client.get(make_url("/err"))
        assert "conn failed" in str(ei2.value)

        # Second call should exhaust the single-use exception and then raise the not-found RuntimeError
        with pytest.raises(RuntimeError):
            client.get(make_url("/err"))
