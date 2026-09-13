"""Confirm the GitHub repo's About description and topics are set.

Pure network read against the public GitHub API — no database, no model. A repo
edit, rename, or fresh fork can silently clear the description or topics, and
nothing else in this pipeline would ever notice: eval gate, RLS, and red team all
answer "does the system behave correctly," not "can anyone find it." This answers
the second question.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API_ROOT = "https://api.github.com"


def repo_slug() -> str:
    """owner/repo, from CI env first, then the `origin` remote."""
    env_slug = os.environ.get("GITHUB_REPOSITORY")
    if env_slug:
        return env_slug

    url = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise ValueError(f"origin remote is not a github.com URL: {url}")
    return match.group(1)


def fetch(slug: str) -> dict:
    request = urllib.request.Request(f"{API_ROOT}/repos/{slug}")
    request.add_header("Accept", "application/vnd.github+json")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def main() -> int:
    slug = repo_slug()

    try:
        repo = fetch(slug)
    except urllib.error.HTTPError as error:
        print(f"FAIL  could not read {slug} from the GitHub API: {error.code} {error.reason}")
        return 1

    description = (repo.get("description") or "").strip()
    topics = repo.get("topics") or []

    failures = 0

    if description:
        print(f"PASS  description set ({len(description)} chars): {description}")
    else:
        print("FAIL  About description is empty")
        failures += 1

    if topics:
        print(f"PASS  {len(topics)} topics: {', '.join(sorted(topics))}")
    else:
        print("FAIL  no topics set")
        failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
