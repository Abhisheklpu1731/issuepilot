"""Offline evaluation on a repository's own history.

Labels: hold out the most recent 20% of labelled issues, train on the rest, measure
precision/recall of the suggested labels.
Duplicates: for issues GitHub marked as duplicates (label "duplicate"), check whether the
referenced original appears in the top-3 matches. Needs issues whose body mentions "#<n>".

    python scripts/evaluate.py --issues data/issues.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.labels import LabelSuggester  # noqa: E402
from app.similarity import SimilarityIndex  # noqa: E402


def eval_labels(issues):
    labelled = sorted([i for i in issues if i["labels"]], key=lambda i: i["number"])
    cut = int(len(labelled) * 0.8)
    train, test = labelled[:cut], labelled[cut:]
    sugg = LabelSuggester(train)
    tp = fp = fn = 0
    for i in test:
        pred = {l for l, _ in sugg.suggest(i)}
        gold = set(i["labels"]) & set(sugg.labels)
        tp += len(pred & gold); fp += len(pred - gold); fn += len(gold - pred)
    p = tp / (tp + fp) if tp + fp else 0
    r = tp / (tp + fn) if tp + fn else 0
    return {"train": len(train), "test": len(test), "labels": sugg.labels,
            "precision": round(p, 3), "recall": round(r, 3)}


def eval_duplicates(issues, backend):
    by_num = {i["number"]: i for i in issues}
    dups = []
    for i in issues:
        if "duplicate" in [l.lower() for l in i["labels"]]:
            refs = [int(n) for n in re.findall(r"#(\d+)", i["body"]) if int(n) in by_num and int(n) < i["number"]]
            if refs:
                dups.append((i, refs[0]))
    if not dups:
        return {"note": "no duplicate-labelled issues referencing an earlier issue"}
    hits = 0
    for issue, original in dups:
        pool = [x for x in issues if x["number"] < issue["number"]]
        idx = SimilarityIndex(pool, backend=backend)
        hits += original in [m.number for m in idx.search(issue, k=3, threshold=0.0)]
    return {"duplicate_pairs": len(dups), "hit@3": round(hits / len(dups), 3)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--issues", default="data/issues.json")
    ap.add_argument("--backend", default="auto")
    args = ap.parse_args()
    issues = json.loads(Path(args.issues).read_text())
    print(json.dumps({"labels": eval_labels(issues), "duplicates": eval_duplicates(issues, args.backend)}, indent=2))
