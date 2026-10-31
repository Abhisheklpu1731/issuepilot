"""Duplicate detection: find past issues most similar to a new one.

Two backends:
- "embeddings": sentence-transformers (all-MiniLM-L6-v2), cosine similarity. Better on paraphrases.
- "tfidf": scikit-learn TF-IDF (word 1-2 grams), cosine similarity. No model download; used in tests.
"""

from dataclasses import dataclass

import numpy as np


def issue_text(issue):
    return f"{issue.get('title', '')}\n{issue.get('body') or ''}".strip()


@dataclass
class Match:
    number: int
    title: str
    url: str
    score: float


class SimilarityIndex:
    def __init__(self, issues, backend="auto"):
        self.issues = issues
        self.backend = self._pick_backend(backend)
        texts = [issue_text(i) for i in issues]
        if self.backend == "embeddings":
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")
            self._matrix = self._model.encode(texts, normalize_embeddings=True) if texts else np.zeros((0, 384))
        else:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
            self._matrix = self._vec.fit_transform(texts) if texts else None

    @staticmethod
    def _pick_backend(backend):
        if backend != "auto":
            return backend
        try:
            import sentence_transformers  # noqa: F401
            return "embeddings"
        except ImportError:
            return "tfidf"

    def _scores(self, text):
        if not self.issues:
            return np.array([])
        if self.backend == "embeddings":
            q = self._model.encode([text], normalize_embeddings=True)[0]
            return self._matrix @ q
        q = self._vec.transform([text])
        return (self._matrix @ q.T).toarray().ravel()   # TF-IDF rows are L2-normalised → dot = cosine

    def search(self, issue, k=3, threshold=0.35, exclude_number=None):
        scores = self._scores(issue_text(issue))
        order = np.argsort(-scores)
        out = []
        for idx in order:
            past = self.issues[idx]
            if past["number"] == exclude_number:
                continue
            if scores[idx] < threshold or len(out) == k:
                break
            out.append(Match(past["number"], past["title"], past.get("html_url", ""), round(float(scores[idx]), 3)))
        return out
