import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.github import verify_signature
from app.labels import LabelSuggester
from app.main import create_app
from app.similarity import SimilarityIndex


def _issue(n, title, body, labels):
    return {"number": n, "title": title, "body": body, "labels": labels,
            "html_url": f"https://github.com/o/r/issues/{n}", "user": "u"}


BUGS = [
    ("App crashes on startup with KeyError", "Traceback shows KeyError 'config' when launching"),
    ("Login button throws TypeError", "Clicking login gives TypeError undefined is not a function"),
    ("Server returns 500 on file upload", "Uploading a large file gives internal server error 500"),
    ("Crash when database connection drops", "Exception raised and process exits when db disconnects"),
    ("Memory leak in websocket handler", "RAM keeps growing, process crashes after hours"),
    ("Error parsing dates in CSV import", "ValueError traceback when importing csv with dates"),
]
DOCS = [
    ("Typo in installation guide", "The README says pip install fastapi[al] instead of [all]"),
    ("Docs missing example for middleware", "Documentation page has no example for custom middleware"),
    ("Broken link in tutorial page", "The link to the deployment docs returns 404 in the tutorial"),
    ("Clarify docs on environment variables", "The documentation does not explain env var precedence"),
    ("Add docs for testing with pytest", "Please document how to write tests for endpoints"),
]
FEATURES = [
    ("Support for async database sessions", "Would be great to add native async session support feature"),
    ("Add dark mode to the dashboard", "Feature request: a dark theme option for the UI"),
    ("Allow custom JSON encoder", "Request to support plugging in a custom JSON encoder"),
    ("Add rate limiting option", "It would be nice to have built in rate limiting feature"),
    ("Export reports as PDF", "Feature request to export the reports page as a PDF file"),
]


@pytest.fixture
def issues():
    out, n = [], 1
    for group, label in ((BUGS, "bug"), (DOCS, "docs"), (FEATURES, "enhancement")):
        for title, body in group:
            out.append(_issue(n, title, body, [label]))
            n += 1
    return out


@pytest.fixture
def client(issues, tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    return TestClient(create_app(issues, db_path=str(tmp_path / "t.db"), backend="tfidf"))


def test_duplicate_found(issues):
    idx = SimilarityIndex(issues, backend="tfidf")
    new = {"title": "App crashes at startup with KeyError config", "body": "KeyError traceback on launch"}
    matches = idx.search(new, threshold=0.1)
    assert matches and matches[0].number == 1


def test_no_duplicate_for_unrelated(issues):
    idx = SimilarityIndex(issues, backend="tfidf")
    assert idx.search({"title": "Kubernetes helm chart", "body": "helm values yaml"}, threshold=0.35) == []


def test_label_suggestion(issues):
    sugg = LabelSuggester(issues)
    assert sugg.trained
    top = sugg.suggest({"title": "Crash with TypeError traceback", "body": "exception when clicking save"})
    assert top[0][0] == "bug"
    top = sugg.suggest({"title": "Documentation typo", "body": "the docs page has a broken link"})
    assert top[0][0] == "docs"


def test_signature_verification():
    body = b'{"a": 1}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3cret", body, sig)
    assert not verify_signature("s3cret", body, "sha256=bad")
    assert not verify_signature("s3cret", body, None)


def test_triage_preview_posts_nothing(client):
    r = client.post("/triage", json={"title": "Server error 500 on upload", "body": "internal server error"})
    data = r.json()
    assert r.status_code == 200
    assert data["suggested_labels"][0]["label"] == "bug"
    assert data["reply_source"] == "template"
    assert client.get("/pending").json() == []


def test_webhook_then_approval_flow(client):
    event = {"action": "opened", "repository": {"full_name": "o/r"},
             "issue": {"number": 99, "title": "Typo in installation guide README",
                       "body": "pip install command in README has a typo", "labels": [],
                       "user": {"login": "newbie"}, "html_url": ""}}
    r = client.post("/webhook/github", content=json.dumps(event), headers={"X-GitHub-Event": "issues"})
    pid = r.json()["pending_id"]
    assert r.json()["possible_duplicates"][0]["number"] == 7

    pending = client.get("/pending").json()
    assert [p["id"] for p in pending] == [pid]

    r = client.post(f"/pending/{pid}/approve", json={"reply": "Thanks, duplicate of #7.", "labels": ["docs"]})
    assert r.json() == {"approved": pid, "dry_run": True, "comment_url": None, "labels": ["docs"]}
    assert client.get("/pending").json() == []
    assert client.post(f"/pending/{pid}/approve").status_code == 404


def test_webhook_ignores_other_actions(client):
    event = {"action": "closed", "issue": {"number": 1, "title": "x"}}
    r = client.post("/webhook/github", content=json.dumps(event), headers={"X-GitHub-Event": "issues"})
    assert "ignored" in r.json()


def _fake_github(monkeypatch, handler):
    import httpx
    import app.github as gh
    real = httpx.Client
    monkeypatch.setattr(gh.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return gh


def _items(start, n):
    return [{"number": i, "title": f"t{i}", "body": "", "labels": [], "html_url": "", "user": {"login": "u"}}
            for i in range(start, start + n)]


def test_fetch_stops_at_pagination_limit(monkeypatch):
    import httpx
    def handler(req):
        page = int(req.url.params["page"])
        return httpx.Response(422) if page > 2 else httpx.Response(200, json={"items": _items(page * 100, 100)})
    gh = _fake_github(monkeypatch, handler)
    assert len(gh.fetch_issues("o/r", 500)) == 200     # keeps the 2 pages it got instead of crashing


def test_fetch_last_page_and_pr_filter(monkeypatch):
    import httpx
    def handler(req):
        items = _items(1, 30) + [{**_items(99, 1)[0], "pull_request": {}}]
        return httpx.Response(200, json={"items": items})
    gh = _fake_github(monkeypatch, handler)
    assert len(gh.fetch_issues("o/r", 300)) == 30


def test_fetch_rate_limit_message(monkeypatch):
    import httpx
    gh = _fake_github(monkeypatch, lambda req: httpx.Response(403))
    with pytest.raises(RuntimeError, match="rate limit"):
        gh.fetch_issues("o/r", 100)
