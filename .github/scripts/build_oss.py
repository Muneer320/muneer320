#!/usr/bin/env python3
"""Refresh only the curated OSS block using public GitHub pull-request data."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START, END = "<!--OSS:START-->", "<!--OSS:END-->"
QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url state isDraft mergedAt author { login }
        repository { nameWithOwner isPrivate }
      }
    }
  }
}
"""


def fetch_prs(user: str, excluded_owners: list[str]) -> list[dict]:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GH_TOKEN or GITHUB_TOKEN is required")
    query = f"is:pr is:public author:{user}" + "".join(
        f" -user:{owner}" for owner in excluded_owners
    )
    rows, cursor = [], None
    while True:
        request = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": QUERY, "variables": {"q": query, "after": cursor}}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "User-Agent": f"{user}-oss-profile"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if payload.get("errors"):
            raise ValueError("GitHub GraphQL errors: " + str(payload["errors"]))
        result = payload["data"]["search"]
        if result["issueCount"] > 1000:
            raise ValueError("Search exceeds GitHub's 1,000-result limit; narrow the query first")
        if any(not row for row in result["nodes"]):
            raise ValueError("GitHub returned an unavailable PR; refusing a partial update")
        rows.extend(result["nodes"])
        page = result["pageInfo"]
        if not page["hasNextPage"]:
            break
        if not page["endCursor"] or page["endCursor"] == cursor:
            raise ValueError("GitHub returned a non-advancing search cursor")
        cursor = page["endCursor"]
    if len(rows) != result["issueCount"]:
        raise ValueError("Incomplete GitHub search; keeping the last successful README")
    return rows


def select_prs(rows: list[dict], config: dict) -> tuple[list[dict], list[str]]:
    projects = {r["repository"].lower(): r for r in config["projects"]}
    excluded = {r.lower() for r in config["exclude_repositories"]}
    owners = {owner.lower() for owner in config["exclude_owners"]}
    selected, unreviewed, seen = [], set(), set()
    for row in rows:
        repo = row["repository"]; name = repo["nameWithOwner"]; key = name.lower()
        if (repo["isPrivate"] or key in excluded or key.split("/")[0] in owners
                or (row.get("author") or {}).get("login", "").lower() != config["user"].lower()):
            continue
        if row["state"] not in {"OPEN", "MERGED"}:
            continue
        if key not in projects:
            unreviewed.add(name)
            continue
        if (row["state"] == "MERGED") != bool(row["mergedAt"]):
            raise ValueError(f"{name} #{row['number']}: inconsistent merged state")
        if row["url"] not in seen:
            selected.append(row); seen.add(row["url"])
    return selected, sorted(unreviewed, key=str.lower)


def md(text: str) -> str:
    text = escape(text.replace("\n", " ").replace("\r", " ")).replace("|", "&#124;")
    for character in ("\\", "[", "]", "*", "_", "`"):
        text = text.replace(character, "\\" + character)
    return text


def build_block(rows: list[dict], config: dict, stamp: str) -> str:
    merged, opened = defaultdict(list), defaultdict(list)
    for row in rows:
        (merged if row["state"] == "MERGED" else opened)[row["repository"]["nameWithOwner"].lower()].append(row)
    merged_count = sum(map(len, merged.values())); open_count = sum(map(len, opened.values()))
    lines = [START, "", '<table>', '<tr>']
    for count, label in [(merged_count, "Merged OSS PRs"), (len(merged), "Projects with merged work"), (open_count, "Open OSS PRs")]:
        lines.append(f'<td align="center" width="33%"><h3>{count}</h3><sub><b>{label}</b></sub></td>')
    lines += ['</tr>', '</table>', '',
              f'<sub>Selected upstream OSS projects · Updated {stamp} by [GitHub Actions](.github/workflows/profile-stats.yml). [Project selection](.github/oss-projects.json).</sub>',
              '', '### Merged contributions', '', '| Project | Contribution | Merged PRs |', '|---|---|---|']
    for project in config["projects"]:
        key = project["repository"].lower(); prs = sorted(merged[key], key=lambda p: p["number"])
        if not prs:
            continue
        links = " · ".join(f"[#{p['number']}]({p['url']})" for p in prs)
        summary = project["summary"] or "; ".join(p["title"] for p in prs[-3:])
        count_label = f"{len(prs)} merged PR" + ("s" if len(prs) != 1 else "")
        lines.append(f"| **[{md(project['label'])}](https://github.com/{project['repository']})** | **{count_label}.** {md(summary)} | {links} |")
    lines += ['', '<details>', f'<summary><b>Open pull requests · {open_count} PRs across {len(opened)} projects</b></summary>', '', '| Project | Pull requests |', '|---|---|']
    for project in config["projects"]:
        prs = sorted(opened[project["repository"].lower()], key=lambda p: p["number"])
        if not prs:
            continue
        links = '<br>'.join(f"[#{p['number']}: {md(p['title'])}{' (draft)' if p['isDraft'] else ''}]({p['url']})" for p in prs)
        lines.append(f"| [{md(project['label'])}](https://github.com/{project['repository']}) | {links} |")
    lines += ['', '</details>', '', END]
    return "\n".join(lines)


def replace_block(content: str, block: str) -> str:
    if content.count(START) != 1 or content.count(END) != 1:
        raise ValueError("README must contain exactly one OSS marker pair")
    before, rest = content.split(START); previous, after = rest.split(END)
    return before + block + after


def main() -> None:
    config = json.loads((ROOT / ".github/oss-projects.json").read_text(encoding="utf-8"))
    if os.environ.get("PROFILE_USER", config["user"]).lower() != config["user"].lower():
        raise ValueError("Set the profile owner in oss-projects.json before running for another user")
    path = ROOT / "README.md"; original = path.read_text(encoding="utf-8")
    replace_block(original, START + "\n" + END)  # Validate boundaries before API work.
    rows, unreviewed = select_prs(fetch_prs(config["user"], config["exclude_owners"]), config)
    block = build_block(rows, config, datetime.now(timezone.utc).strftime("%d %b %Y"))
    updated = replace_block(original, block)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
    summary = f"OSS refreshed: {sum(p['state']=='MERGED' for p in rows)} merged; {sum(p['state']=='OPEN' for p in rows)} open.\n"
    if unreviewed:
        summary += "\nNot counted; review before adding to oss-projects.json:\n" + "\n".join(f"- {name}" for name in unreviewed) + "\n"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
