# dvd-rw

Speed-optimized request recording and replaying for HTTPX.

dvd-rw aims to provide a vcrpy-like workflow with a focus on high scalability: efficient lookups and minimal overhead
when recording/replaying thousands of requests. Core components use Pydantic v2 models for structured data and JSON
serialization. A keyed index enables O(1)-ish replay lookups.

## Quickstart

```python
from dvd_rw.loader import DVDLoader
from dvd_rw.models import Matcher

with DVDLoader(
        file_path="/tmp/example.dvd.json",
        match_on=[Matcher.host, Matcher.method, Matcher.path, Matcher.query],
        extra_matchers=[],
) as dvd:
    # If the file did not exist yet, dvd.from_file will be False (recording allowed)
    # On later runs when the file exists, dvd.from_file will be True (replay only)
    # Do some requests here
    ...
```
## Why dvd-rw?

If your use-case fits the request-replay features of the vcr(py), library, but you need faster performance,
dvd-rw provides:

- Fast lookups for requestsvia a keyed index; only a small bucket is scanned per request
- Built in matchers (host, query, ...) are provided in a performance-optimized manner
- DVD loading is optimized for speed by leveraging Pydantic where possible

## Next Steps

The current version has minimal functionality: only HTTPX support, no cassette modes, etc...

The next versions are focused on improving the experience for my primary use case, testing:

- PyTest integration (through a fixture and mark) to save DVDs for given test(s), allowing you to run against a real
  server once, and store the results for the next test run
    - A PyTest hook to only save the dvd in case of a successful test
- Filtering which requests/request parts to store (functionality similar to before record request/response in vcrpy)

Long-term, I want to keep performance as a main goal:

- Reduce dvd file size when saved through compression, if this is possible in a performant manner
- Move file IO into rust

Feel free to open an issue for functionality that would make this library better

## Installation

Using uv (preferred):

```
uv pip install -e . --group dev
```

Notes:

- -e installs in editable mode for local development.
- --group dev will include pytest for running the test suite.

Using pip (alternative):

```
python -m venv .venv && source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest
```

## Functionality

DVDLoader only saves on successful exit when changes were made (tracked via dvd.dirty). If the DVD was loaded from
file (from_file=True), calling dvd.record_request (i.e., encountering an unsaved request) raises CannotRecord to prevent
accidental writes.

Exception semantics: when recording, if an httpx request raises, the patcher records the exception class and message. On
replay, the same exception type is reconstructed and raised (falling back to httpx.RequestError if constructor
signatures don’t match exactly). This is powered by RequestExceptionInfo in dvd_rw.models.

## Matching and performance

- Builtin matchers are both hashed (for indexing) and compared (for equality) using these features: host, method, path,
  query, headers, scheme.
- You can customize match_on to include only what you need; e.g., not matching on host
- extra_matchers is a list of callables taking (recorded_request, incoming_request) and returning bool, applied after
  the hash bucket filter. Use this to build your own matching logic not supported by the base matcher(s)
- For high throughput:
    - Use match on over extra matchers as much as possible. Generally, the more matchers you have,
      the faster request lookup can run.

## Response selection semantics

DVD.get_response(request) returns the first matching response whose per-index _match_counts < 1, then increments. Each
recorded pair is therefore returned at most once by default. If you need cycling or multiple uses, adapt the logic or
build a higher-level helper.

## Running tests

```
pytest -q
```
