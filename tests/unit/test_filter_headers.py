from dvd_rw.models import DVD, Request, Response, Matcher


def _resp(body: bytes = b"ok", status: int = 200):
    return Response(status=status, headers=[("X-Other", "1")], body=body)


def test_filter_headers_strips_on_record_and_matches_case_insensitive():
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
        filter_headers=["authorization"],
    )

    req = Request(
        headers=[("Authorization", "Bearer abc"), ("X-Other", "1")],
        method="GET",
        url="https://example.com/api/items?id=1",
    )

    res = _resp(b"A")
    dvd.record_request(req, res)

    # Recorded request should have Authorization removed but keep X-Other
    recorded_req, recorded_res = dvd.recorded_requests[0]
    assert ("Authorization", "Bearer abc") not in recorded_req.headers
    assert ("X-Other", "1") in recorded_req.headers

    # Lookup should succeed even if incoming request has AUTHORIZATION in different case
    lookup_req = Request(
        headers=[("AUTHORIZATION", "Bearer abc"), ("X-Other", "1")],
        method="GET",
        url="https://example.com/api/items?id=1",
    )
    got = dvd.get_response(lookup_req)
    assert got == res
    # by default, second call should return None due to single-use semantics
    assert dvd.get_response(lookup_req) is None


def test_filter_headers_before_hook_receives_filtered_request():
    seen_headers = []

    def before(r: Request):
        seen_headers.append(tuple(r.headers))
        return r

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
        filter_headers=["authorization"],
        before_record_request=before,
    )

    req = Request(
        headers=[("Authorization", "Bearer abc"), ("X-Other", "1")],
        method="GET",
        url="https://example.com/",
    )
    res = _resp(b"B")
    dvd.record_request(req, res)

    # The hook should have seen headers without Authorization
    assert len(seen_headers) == 1
    assert ("X-Other", "1") in seen_headers[0]
    assert not any(k.lower() == "authorization" for k, _ in seen_headers[0])


def test_filter_headers_only_affects_listed_headers():
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
        filter_headers=["authorization"],
    )

    req = Request(
        headers=[("X-Other", "1")],
        method="GET",
        url="https://example.com/",
    )

    res = _resp(b"C")
    dvd.record_request(req, res)

    # A lookup with different non-filtered header should not match when headers are part of match_on
    lookup_req = Request(
        headers=[("X-Other", "2")],
        method="GET",
        url="https://example.com/",
    )
    assert dvd.get_response(lookup_req) is None
