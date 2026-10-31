"""Fetch a repository's past issues so IssuePilot can learn its labels and find duplicates.

    python scripts/index_repo.py fastapi/fastapi --max 500 --out data/issues.json

Set GITHUB_TOKEN to avoid the 60 requests/hour unauthenticated limit.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.github import fetch_issues  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="owner/name")
    ap.add_argument("--max", type=int, default=500)
    ap.add_argument("--out", default="data/issues.json")
    args = ap.parse_args()

    issues = fetch_issues(args.repo, args.max)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(issues, indent=1))
    labels = Counter(l for i in issues for l in i["labels"])
    print(f"Saved {len(issues)} issues to {args.out}")
    print("Top labels:", labels.most_common(10))
