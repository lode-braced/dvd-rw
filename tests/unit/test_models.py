from dvd_rw.models import DVD, Request, Response


def test_dvd_create():
    instance = DVD(
        recorded_requests=[
            (
                Request(headers=[], method="GET", url="https://example.com"),
                Response(body="Hello World!".encode(), headers=[], status=200),
            )
        ]
    )
    assert instance
