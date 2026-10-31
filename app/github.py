"""Minimal GitHub REST client: fetch past issues, post an approved comment, apply labels,
and verify webhook signatures."""

import hashlib
import hmac
import os

import httpx

API = "https://api.github.com"


def _headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def normalize(raw):
    return {
        "number": raw["number"],
        "title": raw["title"],
        "body": raw.get("body") or "",
        "labels": [l["name"] for l in raw.get("labels", [])],
        "state": raw.get("state", "open"),
        "html_url": raw.get("html_url", ""),
        "user": (raw.get("user") or {}).get("login", ""),
    }


MAX_PAGES = 10   # GitHub serves at most ~1,000 results per listing


def fetch_issues(repo, max_issues=500):
    """Fetch the most recent open + closed issues, excluding pull requests.

    Uses the search API with `is:issue`, so pull requests never use up pages (repos like
    psf/requests are mostly PRs). Stops early, keeping what it has, if GitHub refuses
    deeper pages.
    """
    issues, page = [], 1
    with httpx.Client(headers=_headers(), timeout=30) as client:
        while len(issues) < max_issues and page <= MAX_PAGES:
            r = client.get(f"{API}/search/issues", params={
                "q": f"repo:{repo} is:issue", "sort": "created", "order": "desc",
                "per_page": 100, "page": page})
            if r.status_code == 422 and issues:
                break                          # past the pagination limit: keep what we have
            if r.status_code in (403, 429):
                if issues:
                    break                      # rate limited mid-way: use what we already have
                raise RuntimeError("GitHub rate limit reached. Wait a minute, or add a GITHUB_TOKEN secret.")
            if r.status_code == 422:
                raise RuntimeError(f"Repository '{repo}' not found or not public.")
            r.raise_for_status()
            batch = r.json().get("items", [])
            issues += [normalize(i) for i in batch if "pull_request" not in i]
            if len(batch) < 100:
                break                          # last page
            page += 1
    return issues[:max_issues]


def post_comment(repo, number, body):
    r = httpx.post(f"{API}/repos/{repo}/issues/{number}/comments",
                   headers=_headers(), json={"body": body}, timeout=30)
    r.raise_for_status()
    return r.json()["html_url"]


def add_labels(repo, number, labels):
    r = httpx.post(f"{API}/repos/{repo}/issues/{number}/labels",
                   headers=_headers(), json={"labels": labels}, timeout=30)
    r.raise_for_status()


def verify_signature(secret, payload, signature_header):
    """GitHub signs webhook payloads with HMAC-SHA256 in the X-Hub-Signature-256 header."""
    if not secret:
        return True   # verification disabled for local testing
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
