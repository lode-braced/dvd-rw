from pydantic import Base64Encoder

from dvd_rw.models import DVD, Request, Response, Matcher


def _resp(body: bytes = b"ok", status: int = 200):
    return Response(
        status=status, headers=[("X-Test", "1")], body=Base64Encoder.encode(body)
    )


def test_before_record_request_can_modify_url_and_replay_matches():
    # Hook that normalizes every request URL to a fixed canonical value
    def before(r: Request) -> Request:
        return Request(
            headers=r.headers, method=r.method, url="https://example.com/canonical"
        )

    dvd = DVD(
        recorded_requests=[],
        from_file=False,
        match_on=[
            Matcher.host,
            Matcher.method,
            Matcher.path,
            Matcher.query,
            Matcher.headers,
            Matcher.scheme,
        ],
        extra_matchers=[],
        before_record_request=before,
    )

    original_req = Request(
        headers=[("X-Test", "1")],
        method="GET",
        url="https://other.example.org/some/path?x=1",
    )
    res = _resp(b"normalized")

    # Record with an arbitrary URL; it should be transformed by the hook
    dvd.record_request(original_req, res)

    # Validate that the recorded request was normalized
    recorded_req, recorded_res = dvd.recorded_requests[0]
    assert recorded_res == res
    assert recorded_req.url == "https://example.com/canonical"

    # Lookup using a totally different URL should still match because the same
    # normalization is applied during replay lookup
    lookup_req = Request(
        headers=[("X-Test", "1")],
        method="GET",
        url="https://another.host/elsewhere?utm=tracking",
    )

    got = dvd.get_response(lookup_req)
    assert got == res

    # Single-use semantics: second call should return None
    assert dvd.get_response(lookup_req) is None
