import json

import httpx

from dvd_rw.loader import DVDLoader
from dvd_rw.models import DVD, Request, Response, Matcher


def _strip_secret_key(resp: Response) -> Response:
    if resp.body is None:
        return resp
    try:
        data = json.loads(bytes(resp.body).decode("utf-8"))
    except Exception:
        return resp
    # Remove the sensitive key if present
    data.pop("secret", None)
    new_body = json.dumps(data).encode("utf-8")
    return Response(status=resp.status, headers=list(resp.headers), body=new_body)


def test_before_record_response_removes_key_from_json_body_unit():
    dvd = DVD(
        recorded_requests=[],
        from_file=False,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
        before_record_response=_strip_secret_key,
    )

    req = Request(headers=[], method="GET", url="https://example.com/api")
    original_payload = {"user": "bob", "secret": "tok", "ok": True}
    res = Response(
        status=200,
        headers=[("Content-Type", "application/json")],
        body=json.dumps(original_payload).encode("utf-8"),
    )

    dvd.record_request(req, res)

    assert len(dvd.recorded_requests) == 1
    recorded_req, recorded_res = dvd.recorded_requests[0]
    assert recorded_req.url == req.url
    assert isinstance(recorded_res, Response)

    body_after = json.loads(bytes(recorded_res.body).decode("utf-8"))  # type: ignore[arg-type]
    assert "secret" not in body_after
    assert body_after["user"] == "bob"
    assert body_after["ok"] is True


def test_before_record_response_with_loader_and_replay(tmp_path):
    file_path = tmp_path / "dvd_before_res.json"

    # Record mode: a transport that returns a JSON body with a sensitive key
    transport_record = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            headers=[("Content-Type", "application/json")],
            json={"user": "bob", "secret": "tok", "ok": True},
        )
    )

    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
        before_record_response=_strip_secret_key,
    ) as dvd:
        client = httpx.Client(transport=transport_record)
        resp = client.get("https://example.com/api")
        assert resp.status_code == 200
        # Ensure recording happened and the stored response body was sanitized
        assert len(dvd.recorded_requests) == 1
        _, rec_res = dvd.recorded_requests[0]
        body_rec = json.loads(bytes(rec_res.body).decode("utf-8"))  # type: ignore[arg-type]
        assert "secret" not in body_rec
        assert body_rec["user"] == "bob"
        assert body_rec["ok"] is True

    # Replay mode: ensure we get the sanitized body and do not hit the network
    def fail_handler(request):  # pragma: no cover - should not be called
        raise AssertionError("Network should not be hit during replay")

    with DVDLoader(
        file_path=file_path,
        match_on=[Matcher.host, Matcher.method, Matcher.path],
        extra_matchers=[],
    ) as _dvd2:
        client = httpx.Client(transport=httpx.MockTransport(fail_handler))
        resp2 = client.get("https://example.com/api")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert "secret" not in data2
        assert data2["user"] == "bob"
        assert data2["ok"] is True
