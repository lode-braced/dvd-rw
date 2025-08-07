import enum
from collections import defaultdict
from collections.abc import Hashable
from functools import cached_property
from typing import Callable, Dict, Iterator
from urllib.parse import urlparse, ParseResult, parse_qs

from pydantic import BaseModel


class Request(BaseModel):
    headers: list[tuple[str, str]]
    method: str
    url: str

    @cached_property
    def _url_parts(self) -> ParseResult:
        return urlparse(self.url)

    @property
    def host(self) -> str:
        return self._url_parts.hostname

    @property
    def path(self) -> str:
        return self._url_parts.path

    @property
    def query(self) -> dict[str, list[str]]:
        return parse_qs(self._url_parts.query)

    @property
    def scheme(self) -> str:
        return self._url_parts.scheme


class Response(BaseModel):
    status: int
    headers: list[tuple[str, str]]
    body: bytes | None


class Matcher(enum.StrEnum):
    host = "host"
    method = "method"
    path = "path"
    query = "query"
    headers = "headers"
    scheme = "scheme"


BUILTIN_MATCHER_HASHERS: dict[Matcher, Callable[[Request], Hashable]] = {
    Matcher.host: lambda r: r.host,
    Matcher.method: lambda r: r.method,
    Matcher.path: lambda r: r.path,
    Matcher.query: lambda r: tuple(r.query.items()),
    Matcher.headers: lambda r: tuple(r.headers),
    Matcher.scheme: lambda r: r.scheme,
}

BUILTIN_MATCHER_COMPARATORS: dict[Matcher, Callable[[Request, Request], bool]] = {
    Matcher.host: lambda r1, r2: r1.host == r2.host,
    Matcher.method: lambda r1, r2: r1.method == r2.method,
    Matcher.path: lambda r1, r2: r1.path == r2.path,
    Matcher.query: lambda r1, r2: r1.query == r2.query,
    Matcher.headers: lambda r1, r2: r1.headers == r2.headers,
    Matcher.scheme: lambda r1, r2: r1.scheme == r2.scheme,
}


class CannotRecord(Exception):
    pass


class DVD(BaseModel):
    recorded_requests: list[tuple[Request, Response]]
    _match_counts: dict[int, int] = defaultdict(int)
    # Stores a request response pair and their list index.
    _hashed_requests: Dict[Hashable, list[tuple[int, Request, Response]]] = defaultdict(
        list
    )
    from_file: bool

    match_on: list[Matcher]
    extra_matchers: list[Callable[[Request, Request], bool]] = []

    def _get_request_key(self, request: Request):
        return tuple(
            BUILTIN_MATCHER_HASHERS[matcher](request) for matcher in self.match_on
        )

    def _responses(self, request: Request) -> Iterator[tuple[int, Response]]:
        hashed_key = self._get_request_key(request)
        for index, rec_request, rec_response in self._hashed_requests[hashed_key]:
            if all(matcher(rec_request, request) for matcher in self.extra_matchers):
                yield index, rec_response

    def record_request(self, request: Request, response: Response):
        if self.from_file:
            raise CannotRecord("Cannot record requests when loaded from file.")
        hashed_key = self._get_request_key(request)
        self._hashed_requests[hashed_key].append(
            (len(self.recorded_requests), request, response)
        )
        self.recorded_requests.append((request, response))

    def get_response(self, request: Request) -> Response | None:
        for index, response in self._responses(request):
            if self._match_counts[index] < 1:
                self._match_counts[index] += 1
                return response
        return None
