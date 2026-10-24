#!/usr/bin/env python3
"""Fetch live numbers for the profile into data/stats.json.

Sources: the GitHub REST API (user + repositories), the GraphQL contribution
calendar, and the blog's RSS/Atom feed. Every source is optional: when one fails,
the previous value in stats.json is kept, so a flaky network never blanks the page.
Standard library only; GITHUB_TOKEN is used when present.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = json.loads((ROOT / "data" / "profile.json").read_text())
STATS_PATH = ROOT / "data" / "stats.json"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
UA = "sujayadkesar-profile-refresh"


def log(msg: str) -> None:
    print(f"[refresh] {msg}", file=sys.stderr)


def request(url: str, data: bytes | None = None, accept: str = "application/vnd.github+json") -> bytes:
    headers = {"User-Agent": UA, "Accept": accept}
    if TOKEN and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def gh(path: str):
    return json.loads(request(f"https://api.github.com{path}"))


def fetch_repos(login: str) -> dict:
    repos, page = {}, 1
    while True:
        batch = gh(f"/users/{login}/repos?per_page=100&type=owner&sort=pushed&page={page}")
        for r in batch:
            repos[r["name"]] = {
                "stars": r["stargazers_count"],
                "fork": r["fork"],
                "archived": r["archived"],
                "pushed": (r.get("pushed_at") or "")[:10],
                "language": r.get("language") or "",
            }
        if len(batch) < 100:
            return repos
        page += 1


def fetch_contributions(login: str) -> dict | None:
    if not TOKEN:
        return None
    query = """query($login:String!){user(login:$login){contributionsCollection{contributionCalendar{
      totalContributions weeks{contributionDays{contributionCount date}}}}}}"""
    body = json.dumps({"query": query, "variables": {"login": login}}).encode()
    data = json.loads(request("https://api.github.com/graphql", data=body))
    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    weeks = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in cal["weeks"]]
    return {"total": cal["totalContributions"], "weeks": weeks[-52:]}


def _date(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def fetch_posts(feeds: list[str], limit: int = 5) -> list[dict]:
    atom = "{http://www.w3.org/2005/Atom}"
    for url in feeds:
        try:
            root = ET.fromstring(request(url, accept="application/rss+xml, application/atom+xml, application/xml"))
        except Exception as exc:  # try the next candidate feed
            log(f"feed {url}: {exc}")
            continue
        posts = []
        for item in root.iter("item"):
            posts.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "date": _date(item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")),
            })
        for entry in root.iter(f"{atom}entry"):
            link = entry.find(f"{atom}link[@rel='alternate']")
            if link is None:
                link = entry.find(f"{atom}link")
            posts.append({
                "title": (entry.findtext(f"{atom}title") or "").strip(),
                "link": link.get("href", "") if link is not None else "",
                "date": _date(entry.findtext(f"{atom}published") or entry.findtext(f"{atom}updated")),
            })
        posts = [p for p in posts if p["title"] and p["link"]]
        if posts:
            posts.sort(key=lambda p: p["date"], reverse=True)
            log(f"feed {url}: {len(posts)} posts")
            return posts[:limit]
    return []


def main() -> None:
    old = json.loads(STATS_PATH.read_text()) if STATS_PATH.exists() else {}
    new = dict(old)
    login = PROFILE["login"]

    try:
        user = gh(f"/users/{login}")
        new["user"] = {"public_repos": user["public_repos"], "followers": user["followers"]}
    except Exception as exc:
        log(f"user: {exc}")

    try:
        repos = fetch_repos(login)
        own = {n: r for n, r in repos.items() if not r["fork"]}
        top = max(own.items(), key=lambda kv: kv[1]["stars"])
        # The profile repo is pushed by this workflow every day, so it never counts as "latest".
        latest = max(((k, v) for k, v in own.items() if k != login), key=lambda kv: kv[1]["pushed"])
        new["repos"] = repos
        new["totals"] = {"original_repos": len(own), "stars_original": sum(r["stars"] for r in own.values())}
        new["top_repo"] = {"name": top[0], "stars": top[1]["stars"]}
        new["latest_push"] = {"name": latest[0], "date": latest[1]["pushed"]}
    except Exception as exc:
        log(f"repos: {exc}")

    try:
        contrib = fetch_contributions(login)
        if contrib:
            new["contributions"] = contrib
    except Exception as exc:
        log(f"contributions: {exc}")

    posts = fetch_posts(PROFILE["blog"]["feeds"])
    if posts:
        new["posts"] = posts

    # Only move the "as of" date when something actually changed, so quiet days
    # don't produce a commit.
    strip = lambda d: {k: v for k, v in d.items() if k != "as_of"}
    if strip(new) != strip(old) or "as_of" not in new:
        new["as_of"] = datetime.now(timezone.utc).date().isoformat()
    STATS_PATH.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n")
    log("stats.json " + ("updated" if new != old else "unchanged"))


if __name__ == "__main__":
    main()
