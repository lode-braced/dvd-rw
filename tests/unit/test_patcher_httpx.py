import httpx
import pytest

from dvd_rw.loader import DVDLoader
from dvd_rw.models import Matcher


BASE_URL = "https://example.com"


def make_url(path: str) -> str:
    return f"{BASE_URL}{path}"


def test_record_and_replay_sync(tmp_path):
    file_path = tmp_path / "dvd1.json"

    # Record mode: perform real request via MockTransport and record
    transport_record = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers=[("X-Mode", "record")], content=b"ok-record"
        )
    )

    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ) as dvd:
        client = httpx.Client(transport=transport_record)
        resp = client.get(make_url("/items"))
        assert resp.status_code == 200
        assert resp.content == b"ok-record"
        assert resp.text == "ok-record"
        # Ensure we recorded exactly one request
        assert len(dvd.recorded_requests) == 1

    # Replay mode: ensure we don't hit the transport and get the recorded response
    def fail_handler(request):  # pragma: no cover - should not be called
        raise AssertionError("Network should not be hit during replay")

    transport_replay = httpx.MockTransport(fail_handler)

    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ) as dvd2:
        # Loaded from file, so replay-only
        client = httpx.Client(transport=transport_replay)
        resp2 = client.get(make_url("/items"))
        assert resp2.status_code == 200
        assert resp2.content == b"ok-record"
        assert resp.text == "ok-record"
        # Second call should return None per current semantics; our patcher returns httpx.Response only on replay,
        # so verify None behavior via direct DVD API
        assert dvd2.get_response(dvd2.recorded_requests[0][0]) is None


def test_missing_request_raises_in_replay(tmp_path):
    file_path = tmp_path / "dvd2.json"

    # First, create a DVD with one recording
    transport_record = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"hello")
    )
    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ):
        httpx.Client(transport=transport_record).get(make_url("/only-this"))

    # Now, in replay mode, calling a different URL should raise
    def fail_handler(request):  # pragma: no cover
        raise AssertionError("Should not perform network call")

    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ):
        client = httpx.Client(transport=httpx.MockTransport(fail_handler))
        with pytest.raises(RuntimeError):
            client.get(make_url("/not-present"))


def test_patcher_stack_nested_loaders(tmp_path):
    file_inner = tmp_path / "inner.json"
    file_outer = tmp_path / "outer.json"

    # Prepare inner DVD with one recorded path
    transport_inner = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"inner")
    )
    with DVDLoader(
        file_path=file_inner,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ):
        httpx.Client(transport=transport_inner).get(make_url("/inner"))

    # Outer loader in record mode; inner loader in replay mode nested
    transport_outer = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"outer")
    )

    with DVDLoader(
        file_path=file_outer,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ) as outer_dvd:
        # Stack: [outer]
        with DVDLoader(
            file_path=file_inner,
            match_on=[Matcher.host, Matcher.method, Matcher.path],
            extra_matchers=[],
        ):
            # Stack: [outer, inner(top)]
            # Request that exists in inner should replay
            resp_inner = httpx.Client(
                transport=httpx.MockTransport(
                    lambda req: (_ for _ in ()).throw(AssertionError("no network"))
                )
            )
            resp = resp_inner.get(make_url("/inner"))
            assert resp.status_code == 200
            assert resp.content == b"inner"

            # Request not in inner should raise (since inner from_file=True)
            with pytest.raises(RuntimeError):
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: (_ for _ in ()).throw(AssertionError("no network"))
                    )
                ).get(make_url("/outer"))

        # After inner exits, top-of-stack reverts to outer; now we can record '/outer'
        resp2 = httpx.Client(transport=transport_outer).get(make_url("/outer"))
        assert resp2.status_code == 200
        assert resp2.content == b"outer"
        assert len(outer_dvd.recorded_requests) == 1
