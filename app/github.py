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


def fetch_issues(repo, max_issues=500):
    """Fetch open + closed issues (pull requests excluded)."""
    issues, page = [], 1
    with httpx.Client(headers=_headers(), timeout=30) as client:
        while len(issues) < max_issues:
            r = client.get(f"{API}/repos/{repo}/issues",
                           params={"state": "all", "per_page": 100, "page": page})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            issues += [normalize(i) for i in batch if "pull_request" not in i]
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
