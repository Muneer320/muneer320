#!/usr/bin/env python3
"""Verify that every remote image in the README actually renders.

A plain link checker is not enough here. Services like github-readme-stats
answer HTTP 200 and hand back an SVG that *says* "Something went wrong! ...
Maximum retries exceeded". The badge looks alive to a link checker while
visitors see a grey error tile. This checks the payload, not just the status.
"""

from __future__ import annotations

import concurrent.futures
import re
import sys
import urllib.error
import urllib.request

README = "README.md"
TIMEOUT = 30
RETRIES = 3

# Substrings that mean "this rendered, but rendered an error".
ERROR_MARKERS = (
    "Something went wrong",
    "Maximum retries exceeded",
    "Could not fetch",
    "Bad credentials",
    "NOT FOUND",
    "Invalid username",
    "rate limit",
    "undefined",
)

IMG_SRC = re.compile(r'<img[^>]+src="(https?://[^"]+)"', re.I)
SRCSET = re.compile(r'srcset="(https?://[^"]+)"', re.I)
MD_IMG = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")


def collect() -> list[str]:
    with open(README, encoding="utf-8") as fh:
        src = fh.read()
    urls = set()
    for pattern in (IMG_SRC, SRCSET, MD_IMG):
        urls.update(pattern.findall(src))
    return sorted(urls)


def check(url: str) -> tuple[str, str]:
    last = "unknown error"
    for _ in range(RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (profile-widget-healthcheck)",
                    "Accept": "image/svg+xml,image/*,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
                if resp.status != 200:
                    last = f"HTTP {resp.status}"
                    continue
                if not body:
                    last = "empty body"
                    continue
                text = body.decode("utf-8", "ignore")
                # Only inspect text payloads; PNG/GIF bytes are not searchable.
                if "<svg" in text[:2000].lower():
                    for marker in ERROR_MARKERS:
                        if marker.lower() in text.lower():
                            return url, f"renders an error: {marker!r}"
                return url, ""
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - report whatever went wrong
            last = type(exc).__name__
    return url, last


def main() -> int:
    urls = collect()
    if not urls:
        print("No remote images in README.")
        return 0

    print(f"Checking {len(urls)} remote image(s)...\n")
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for url, problem in pool.map(check, urls):
            if problem:
                failures.append((url, problem))
                print(f"  FAIL  {url}\n        -> {problem}")
            else:
                print(f"  ok    {url}")

    if failures:
        print(f"\n{len(failures)} widget(s) are not rendering correctly.")
        summary = "\n".join(f"- `{u}`, {p}" for u, p in failures)
        print(f"::error::Broken widgets detected:\n{summary}")
        return 1

    print(f"\nAll {len(urls)} widgets render correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
