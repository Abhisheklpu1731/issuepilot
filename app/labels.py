"""Label suggestion, learned from the repository's own past labelled issues.

TF-IDF features + one-vs-rest logistic regression, so each label gets an independent
probability (an issue can be both "bug" and "docs"). Labels with fewer than
`min_examples` past issues are skipped: too little data to learn them.
"""

from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import MultiLabelBinarizer

from .similarity import issue_text


class LabelSuggester:
    def __init__(self, issues, min_examples=3):
        counts = Counter(l for i in issues for l in i.get("labels", []))
        self.labels = sorted(l for l, c in counts.items() if c >= min_examples)
        labelled = [i for i in issues if set(i.get("labels", [])) & set(self.labels)]
        self.trained = len(self.labels) >= 2 and len(labelled) >= 10
        if not self.trained:
            return
        self._mlb = MultiLabelBinarizer(classes=self.labels)
        y = self._mlb.fit_transform([[l for l in i["labels"] if l in self.labels] for i in labelled])
        self._clf = make_pipeline(
            TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1),
            OneVsRestClassifier(LogisticRegression(max_iter=1000, class_weight="balanced")),
        )
        self._clf.fit([issue_text(i) for i in labelled], y)

    def suggest(self, issue, threshold=0.5, top_n=3):
        if not self.trained:
            return []
        probs = self._clf.predict_proba([issue_text(issue)])[0]
        ranked = sorted(zip(self.labels, probs), key=lambda x: -x[1])
        picked = [(l, round(float(p), 3)) for l, p in ranked if p >= threshold][:top_n]
        return picked or [(ranked[0][0], round(float(ranked[0][1]), 3))]   # always offer the best guess
