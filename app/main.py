"""IssuePilot API.

Flow: GitHub "issue opened" webhook → suggest labels + find likely duplicates + draft a reply
→ stored as PENDING → a maintainer approves (optionally editing the reply/labels) → only then
is anything posted to GitHub.

Run:  uvicorn app.main:app --reload
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from . import github
from .drafts import draft_reply
from .labels import LabelSuggester
from .similarity import SimilarityIndex
from .store import PendingStore


class IssueIn(BaseModel):
    title: str
    body: str = ""
    number: int | None = None
    user: str = ""


class ApproveIn(BaseModel):
    reply: str | None = None          # maintainer's edited reply; defaults to the draft
    labels: list[str] | None = None   # maintainer's chosen labels; defaults to suggestions


def load_issues(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else []


def create_app(issues=None, db_path=None, backend=None):
    issues = issues if issues is not None else load_issues(os.getenv("ISSUES_FILE", "data/issues.json"))
    repo = os.getenv("ISSUEPILOT_REPO", "")
    secret = os.getenv("WEBHOOK_SECRET", "")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true" or not os.getenv("GITHUB_TOKEN")

    app = FastAPI(title="IssuePilot", version="0.1.0")
    app.state.index = SimilarityIndex(issues, backend=backend or os.getenv("SIMILARITY_BACKEND", "auto"))
    app.state.labeler = LabelSuggester(issues)
    app.state.store = PendingStore(db_path or os.getenv("DB_PATH", "issuepilot.db"))

    def triage(issue: dict):
        labels = app.state.labeler.suggest(issue)
        dups = app.state.index.search(issue, exclude_number=issue.get("number"))
        reply, source = draft_reply(issue, labels, dups)
        return {
            "issue_number": issue.get("number"),
            "title": issue["title"],
            "suggested_labels": [{"label": l, "score": s} for l, s in labels],
            "possible_duplicates": [d.__dict__ for d in dups],
            "draft_reply": reply,
            "reply_source": source,
        }

    @app.get("/health")
    def health():
        return {"status": "ok", "indexed_issues": len(issues), "similarity_backend": app.state.index.backend,
                "labels_learned": app.state.labeler.labels, "dry_run": dry_run}

    @app.post("/triage")
    def triage_endpoint(issue: IssueIn):
        """Preview triage for any issue text. Stores nothing, posts nothing."""
        return triage(issue.model_dump())

    @app.post("/webhook/github")
    async def webhook(request: Request):
        payload = await request.body()
        if not github.verify_signature(secret, payload, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(401, "invalid signature")
        if request.headers.get("X-GitHub-Event") != "issues":
            return {"ignored": "not an issues event"}
        data = json.loads(payload)
        if data.get("action") != "opened":
            return {"ignored": f"action {data.get('action')}"}
        issue = github.normalize(data["issue"])
        result = triage(issue)
        pid = app.state.store.add(data.get("repository", {}).get("full_name", repo), issue["number"], result)
        return {"pending_id": pid, **result}

    @app.get("/pending")
    def list_pending():
        return app.state.store.list("pending")

    @app.post("/pending/{pid}/approve")
    def approve(pid: int, body: ApproveIn | None = None):
        item = app.state.store.get(pid)
        if not item or item["status"] != "pending":
            raise HTTPException(404, "no pending item with that id")
        result = item["payload"]
        reply = (body and body.reply) or result["draft_reply"]
        labels = (body and body.labels) if body and body.labels is not None else \
            [l["label"] for l in result["suggested_labels"]]
        posted = None
        if not dry_run:
            posted = github.post_comment(item["repo"], item["issue_number"], reply)
            if labels:
                github.add_labels(item["repo"], item["issue_number"], labels)
        result.update({"final_reply": reply, "final_labels": labels, "comment_url": posted})
        app.state.store.decide(pid, "approved", result)
        return {"approved": pid, "dry_run": dry_run, "comment_url": posted, "labels": labels}

    @app.post("/pending/{pid}/reject")
    def reject(pid: int):
        item = app.state.store.get(pid)
        if not item or item["status"] != "pending":
            raise HTTPException(404, "no pending item with that id")
        app.state.store.decide(pid, "rejected")
        return {"rejected": pid}

    return app


app = create_app()
