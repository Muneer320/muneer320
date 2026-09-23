#!/usr/bin/env python3
"""Build the profile stats block from the GitHub API.

Replaces third-party stats cards (github-readme-stats, streak-stats), which
render error images whenever their shared instance is rate-limited. Everything
here is computed from the authenticated GitHub API and committed as plain
Markdown, so it renders even when every external service is down.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

USER = os.environ.get("PROFILE_USER", "Muneer320")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
README = os.environ.get("README_PATH", "README.md")

START = "<!--STATS:START-->"
END = "<!--STATS:END-->"

GRAPHQL = """
query($login: String!) {
  user(login: $login) {
    followers { totalCount }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      contributionCalendar { totalContributions }
    }
    repositories(first: 100, privacy: PUBLIC, ownerAffiliations: OWNER,
                 isFork: false, orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes {
        name
        stargazerCount
        forkCount
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""


def graphql(query: str, variables: dict) -> dict:
    if not TOKEN:
        sys.exit("error: GH_TOKEN or GITHUB_TOKEN must be set")
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": f"{USER}-profile-stats",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: GitHub API returned {exc.code}: {exc.read()[:400]!r}")
    if "errors" in payload:
        sys.exit(f"error: GraphQL errors: {payload['errors']}")
    return payload["data"]


def build_block(data: dict) -> str:
    user = data["user"]
    repos = user["repositories"]["nodes"]
    contrib = user["contributionsCollection"]

    stars = sum(r["stargazerCount"] for r in repos)
    forks = sum(r["forkCount"] for r in repos)

    langs: dict[str, int] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            langs[edge["node"]["name"]] = langs.get(edge["node"]["name"], 0) + edge["size"]
    total_bytes = sum(langs.values()) or 1
    top = sorted(langs.items(), key=lambda kv: kv[1], reverse=True)[:6]

    lang_line = " · ".join(f"{name} {size / total_bytes * 100:.0f}%" for name, size in top)

    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    calendar = contrib["contributionCalendar"]["totalContributions"]

    prs = contrib["totalPullRequestContributions"]
    repo_count = user["repositories"]["totalCount"]

    return "\n".join(
        [
            START,
            "",
            "<sub>"
            f"<b>{repo_count}</b> public repos · "
            f"<b>{stars}</b> stars · "
            f"<b>{forks}</b> forks · "
            f"<b>{calendar:,}</b> contributions · "
            f"<b>{prs}</b> PRs opened (12 mo)"
            "</sub><br>",
            f"<sub>By bytes: {lang_line}</sub><br>",
            f"<sub><i>Verified against the GitHub API on {stamp} by "
            '<a href=".github/workflows/profile-stats.yml">this workflow</a>, '
            "not a third-party stats service.</i></sub>",
            "",
            END,
        ]
    )


def main() -> int:
    data = graphql(GRAPHQL, {"login": USER})
    block = build_block(data)

    with open(README, encoding="utf-8") as fh:
        content = fh.read()

    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(content):
        sys.exit(f"error: markers {START} / {END} not found in {README}")

    updated = pattern.sub(lambda _: block, content)
    if updated == content:
        print("stats unchanged")
        return 0

    with open(README, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(updated)
    print("stats updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
