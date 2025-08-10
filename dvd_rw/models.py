import enum
from collections import defaultdict
from collections.abc import Hashable
from functools import cached_property
from typing import Callable, Dict, Iterator, TypedDict, NotRequired, Unpack
from urllib.parse import urlparse, ParseResult, parse_qs

from pydantic import BaseModel, ImportString, Base64Bytes


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
    body: Base64Bytes | None


class RequestExceptionInfo(BaseModel):
    # Exception type stored as importable reference using Pydantic's ImportString
    exc_type: ImportString[Callable]
    message: str

    @classmethod
    def from_exception(cls, exc: BaseException) -> "RequestExceptionInfo":
        # Store the class object; ImportString will handle serialization/deserialization
        return cls(exc_type=exc.__class__, message=str(exc))  # noqa


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
    Matcher.query: lambda r: tuple(
        {key: tuple(value) for key, value in r.query.items()}.items()
    ),
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


class DVDKwargs(TypedDict, total=False):
    match_on: list["Matcher"]
    extra_matchers: list[Callable[[Request, Request], bool]]
    before_record_request: NotRequired[Callable[[Request], Request | None] | None]


class DVD:
    """Plain Python class managing recorded request/value pairs and fast lookups."""

    def __init__(
        self,
        recorded_requests: list[tuple[Request, Response | RequestExceptionInfo]] | None,
        from_file: bool,
        **kwargs: Unpack[DVDKwargs],
    ) -> None:
        self.recorded_requests: list[
            tuple[Request, Response | RequestExceptionInfo]
        ] = recorded_requests or []
        # Match counts and indices for unified records (responses or exceptions)
        self._match_counts: dict[int, int] = defaultdict(int)
        # Stores a request with its value (Response | RequestExceptionInfo) and their list index.
        self._hashed_requests: Dict[
            Hashable, list[tuple[int, Request, Response | RequestExceptionInfo]]
        ] = defaultdict(list)

        self.from_file: bool = from_file
        self.dirty: bool = False

        _match_on = kwargs.get("match_on")
        _extra_matchers = kwargs.get("extra_matchers")
        _before = kwargs.get("before_record_request")

        self.match_on: list[Matcher] = (
            _match_on
            if _match_on is not None
            else [
                Matcher.host,
                Matcher.method,
                Matcher.path,
                Matcher.query,
                Matcher.headers,
                Matcher.scheme,
            ]
        )
        self.extra_matchers: list[Callable[[Request, Request], bool]] = (
            _extra_matchers or []
        )
        # Hook to decide whether and how to record a request; not serialized
        self.before_record_request: Callable[[Request], Request | None] | None = _before

        # Initialize index based on any provided records
        self.rebuild_index()

    def _apply_before(self, request: Request) -> Request | None:
        """Apply the before_record_request hook if present.

        Returns possibly transformed Request, or None to indicate it should not be recorded.
        Errors in the hook will be treated as a signal to skip recording for safety.
        """
        if self.before_record_request is None:
            return request
        try:
            return self.before_record_request(request)
        except Exception:
            return None

    def can_record(self, request: Request) -> bool:
        """Return True if this request should be recorded given the hook."""
        return self._apply_before(request) is not None

    def rebuild_index(self) -> None:
        # Reset indices and rebuild from recorded_requests
        self._hashed_requests = defaultdict(list)
        self._match_counts = defaultdict(int)
        for idx, (req, val) in enumerate(self.recorded_requests):
            key = self._get_request_key(req)
            self._hashed_requests[key].append((idx, req, val))

    def _get_request_key(self, request: Request):
        return tuple(
            BUILTIN_MATCHER_HASHERS[matcher](request) for matcher in self.match_on
        )

    def _records(
        self, request: Request
    ) -> Iterator[tuple[int, Response | RequestExceptionInfo]]:
        hashed_key = self._get_request_key(request)
        for index, rec_request, rec_value in self._hashed_requests[hashed_key]:
            if all(matcher(rec_request, request) for matcher in self.extra_matchers):
                yield index, rec_value

    def record_request(
        self, request: Request, value: Response | RequestExceptionInfo | BaseException
    ) -> None:
        if self.from_file:
            raise CannotRecord("Cannot record requests when loaded from file.")
        # Apply before_record_request hook to possibly transform or skip
        transformed = self._apply_before(request)
        if transformed is None:
            # Skip recording silently; caller can decide behavior (e.g., just run request)
            return
        if not isinstance(value, (Response, RequestExceptionInfo)):
            # Convert raw exception to serializable info
            value = RequestExceptionInfo.from_exception(value)
        hashed_key = self._get_request_key(transformed)
        self._hashed_requests[hashed_key].append(
            (len(self.recorded_requests), transformed, value)
        )
        self.recorded_requests.append((transformed, value))
        self.dirty = True

    def get_response(self, request: Request) -> Response | None:
        # Backwards-compatible helper that only returns responses.
        for index, rec in self._records(request):
            if self._match_counts[index] < 1 and isinstance(rec, Response):
                self._match_counts[index] += 1
                return rec
        return None

    def get_request(self, request: Request) -> Response | None:
        # Returns Response if matched; raises reconstructed exception if matched exception; else None.
        for index, rec in self._records(request):
            if self._match_counts[index] < 1:
                self._match_counts[index] += 1
                if isinstance(rec, Response):
                    return rec
                # It's an exception; reconstruct and raise
                exc_info = rec  # type: ignore[assignment]
                import httpx

                # ImportString ensures exc_type is the resolved class
                exc_cls = exc_info.exc_type  # type: ignore[assignment]
                # Build an httpx.Request using the provided Request model
                req_obj = httpx.Request(method=request.method, url=request.url)
                try:
                    raise exc_cls(exc_info.message, request=req_obj)
                except TypeError:
                    try:
                        raise exc_cls(exc_info.message)
                    except Exception:
                        raise httpx.RequestError(exc_info.message, request=req_obj)

        return None
