#!/usr/bin/env python3
"""
Performance script for dvd-rw

Generates a DVD with many Request/Response pairs, saves to disk, then loads it
back, printing timings and basic throughput numbers.

Usage (from project root):
  python tests/perf/perf_saveload.py --count 10000 --kb 2 --file /tmp/dvd_perf.json

Notes:
- This is not a pytest test; it's a standalone script for manual performance checks.
- Bytes bodies are serialized to base64 by Pydantic JSON, so expect larger files on disk.
"""

from __future__ import annotations

import argparse
import os
import random
import string
import time
from pathlib import Path

from pydantic import Base64Encoder

from dvd_rw.loader import DVDLoader
from dvd_rw.models import DVD, Matcher, Request, Response


def _random_bytes(n: int) -> bytes:
    # Use deterministic seed if provided via env; otherwise random.
    seed_env = os.environ.get("DVD_PERF_SEED")
    if seed_env is not None:
        rnd = random.Random(int(seed_env))
        return bytes(rnd.getrandbits(8) for _ in range(n))
    return os.urandom(n)


def _build_dataset(count: int, body_bytes: int) -> list[tuple[Request, Response]]:
    # Create some variability but with repeating hosts/paths to simulate realistic caching
    hosts = ["api.example.com", "svc.example.org", "cdn.example.net"]
    methods = ["GET", "POST"]
    paths = [f"/items/{i}" for i in range(50)]
    hdr_keys = ["x-trace-id", "x-req", "accept"]

    out: list[tuple[Request, Response]] = []
    body_payload = _random_bytes(body_bytes)
    print(f"body payload size: {human_bytes(len(body_payload))}")
    for i in range(count):
        host = hosts[i % len(hosts)]
        method = methods[i % len(methods)]
        path = paths[i % len(paths)]
        # Simple numeric query param for hashing variety
        url = f"https://{host}{path}?id={i}&page={(i // 10) % 5}"
        headers = [
            (hdr_keys[0], f"{i:08x}"),
            (
                hdr_keys[1],
                "".join(random.choice(string.ascii_lowercase) for _ in range(8)),
            ),
            (hdr_keys[2], "application/json"),
        ]
        req = Request(headers=headers, method=method, url=url)
        # Reuse the same payload object to reduce Python memory churn while still serializing out fully
        res = Response(
            status=200,
            headers=[("content-type", "application/octet-stream")],
            body=Base64Encoder.encode(body_payload),
        )
        out.append((req, res))
    return out


def human_bytes(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024 or unit == "GB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n} B"


def run(count: int, kb: int, file_path: Path, match_headers: bool = False) -> None:
    body_bytes = kb * 1024

    print("dvd-rw performance run")
    print(f"  count:        {count}")
    print(f"  body size:    {kb} KB ({body_bytes} bytes each)")
    print(f"  output file:  {file_path}")
    print(f"  match headers:{'yes' if match_headers else 'no'}")

    t0 = time.perf_counter()
    dataset = _build_dataset(count, body_bytes)
    t1 = time.perf_counter()
    print(f"build dataset:  {(t1 - t0):.3f}s  ({count / max(t1 - t0, 1e-9):.0f} req/s)")

    match_on = [Matcher.host, Matcher.method, Matcher.path, Matcher.query]
    if match_headers:
        match_on.append(Matcher.headers)

    dvd = DVD(
        recorded_requests=[], from_file=False, match_on=match_on, extra_matchers=[]
    )

    t2 = time.perf_counter()
    for req, res in dataset:
        dvd.record_request(req, res)
    t3 = time.perf_counter()
    print(f"record {count}: {(t3 - t2):.3f}s  ({count / max(t3 - t2, 1e-9):.0f} req/s)")

    loader = DVDLoader(file_path=file_path, match_on=match_on, extra_matchers=[])
    loader.dvd = dvd

    t4 = time.perf_counter()
    loader.save()
    t5 = time.perf_counter()

    size = file_path.stat().st_size
    print(f"save JSON:      {(t5 - t4):.3f}s  -> {human_bytes(size)}")

    # Free references before load timing to avoid disk cache effects (best effort)
    del dataset
    del dvd

    t6 = time.perf_counter()
    loader2 = DVDLoader(file_path=file_path, match_on=match_on, extra_matchers=[])
    loader2.load()
    t7 = time.perf_counter()

    print(
        f"load+index:     {(t7 - t6):.3f}s  (records: {len(loader2.dvd.recorded_requests)})"
    )

    # Measure indexing time alone (rebuild index again for timing purposes)
    t7i = time.perf_counter()
    try:
        loader2.dvd.rebuild_index()
    except AttributeError:
        pass
    t7j = time.perf_counter()
    print(f"index only:     {(t7j - t7i):.3f}s")

    # Optional: sample a few lookups
    sample = [
        Request(
            headers=[("accept", "application/json")],
            method="GET",
            url="https://api.example.com/items/1?id=1&page=0",
        ),
        Request(
            headers=[("accept", "application/json")],
            method="POST",
            url="https://svc.example.org/items/2?id=2&page=0",
        ),
    ]
    t8 = time.perf_counter()
    hits = 0
    for r in sample:
        if loader2.dvd.get_response(r) is not None:
            hits += 1
    t9 = time.perf_counter()
    print(f"sample lookups:  {(t9 - t8):.6f}s  (hits={hits}/{len(sample)})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="dvd-rw save/load perf test")
    parser.add_argument("--count", type=int, default=10_000, help="number of requests")
    parser.add_argument("--kb", type=int, default=2, help="response body size in KB")
    parser.add_argument(
        "--file", type=Path, default=Path("/tmp/dvd_perf.json"), help="output file path"
    )
    parser.add_argument(
        "--match-headers", action="store_true", help="include headers in match key"
    )
    args = parser.parse_args()

    # Ensure parent directory exists
    args.file.parent.mkdir(parents=True, exist_ok=True)

    run(
        count=args.count,
        kb=args.kb,
        file_path=args.file,
        match_headers=args.match_headers,
    )
