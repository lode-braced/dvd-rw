from pydantic import Base64Encoder

from dvd_rw.models import DVD, Request, Response


def test_dvd_create():
    instance = DVD(
        recorded_requests=[
            (
                Request(headers=[], method="GET", url="https://example.com"),
                Response(
                    body=Base64Encoder.encode(b"Hello World!"), headers=[], status=200
                ),
            )
        ],
        from_file=False,
    )
    assert instance
