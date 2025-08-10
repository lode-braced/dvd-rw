import httpx
import pytest

from dvd_rw.loader import DVDLoader
from dvd_rw.models import Matcher

BASE_URL = "https://example.com"

def make_url(path: str) -> str:
    return f"{BASE_URL}{path}"


def test_from_file_allows_passthrough_for_non_recordable(tmp_path):
    file_path = tmp_path / "dvd_from_file_passthrough.json"

    # Stage 1: record a single request so that the file exists
    transport_record = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"replayed")
    )
    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ):
        client = httpx.Client(transport=transport_record)
        resp = client.get(make_url("/replay"))
        assert resp.status_code == 200
        assert resp.content == b"replayed"

    # Stage 2: open from_file mode and set before_record_request to filter
    # - Allow recording/replay only for path '/replay'
    # - For others, return None to indicate non-recordable -> should passthrough
    live_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"live")
    )

    # Only '/replay' is considered recordable; others are non-recordable
    def before(req):
        from urllib.parse import urlparse
        p = urlparse(req.url).path
        if p in ("/replay", "/missing-recordable"):
            return req
        return None

    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
        before_record_request=before,
    ) as dvd:

        # 1) This should replay from the file and NOT hit the live transport
        def fail_handler(_request):  # pragma: no cover - should not be called
            raise AssertionError("Network should not be hit for '/replay'")

        client_replay = httpx.Client(transport=httpx.MockTransport(fail_handler))
        r1 = client_replay.get(make_url("/replay"))
        assert r1.status_code == 200
        assert r1.content == b"replayed"

        # 2) This path is non-recordable -> should passthrough to live transport
        client_live = httpx.Client(transport=live_transport)
        r2 = client_live.get(make_url("/live"))
        assert r2.status_code == 200
        assert r2.content == b"live"

        # 3) A recordable path that isn't in the DVD should still raise in replay mode
        with pytest.raises(RuntimeError):
            client_replay.get(make_url("/missing-recordable"))
