import math
import re
from collections import Counter

_CJK = re.compile(r"[\u3400-\u9fff]+")
_ASCII = re.compile(r"c\+\+|==|!=|<=|>=|->|\+\+|--|[A-Za-z_][A-Za-z0-9_]*|\d+")


def tokenize(text: str) -> list[str]:
    """Character unigrams/bigrams for Chinese, plus ASCII identifiers and numbers."""
    text = text.lower()
    tokens: list[str] = []
    for run in _CJK.findall(text):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    tokens.extend(_ASCII.findall(text))
    return tokens


class BM25Index:
    def __init__(self, texts: list[str], *, k1: float = 1.5, b: float = 0.75):
        self._documents = [Counter(tokenize(text)) for text in texts]
        self._lengths = [sum(document.values()) for document in self._documents]
        self._average_length = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        self._document_frequency: Counter[str] = Counter()
        for document in self._documents:
            self._document_frequency.update(document.keys())
        self._size = len(self._documents)
        self._k1, self._b = k1, b

    def scores(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        if not terms or not self._documents:
            return [0.0] * self._size
        idf = {
            term: math.log(
                1
                + (self._size - self._document_frequency[term] + 0.5)
                / (self._document_frequency[term] + 0.5)
            )
            for term in terms
        }
        result: list[float] = []
        for document, length in zip(self._documents, self._lengths, strict=True):
            score = 0.0
            for term in terms:
                frequency = document.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self._k1 * (
                    1 - self._b + self._b * length / max(self._average_length, 1e-9)
                )
                score += idf[term] * frequency * (self._k1 + 1) / denominator
            result.append(score)
        return result

    def coverage(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        return [
            len(terms.intersection(document)) / max(len(terms), 1) for document in self._documents
        ]

    def coverage_any(self, queries: list[str]) -> list[float]:
        """OR between complete equivalent queries, AND-style coverage within each query."""
        terms = [set(tokenize(query)) for query in queries]
        return [
            max(
                (len(group.intersection(document)) / max(len(group), 1) for group in terms),
                default=0.0,
            )
            for document in self._documents
        ]


def reciprocal_rank_fusion(rankings: list[list[int]], *, k: int = 60) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking, 1):
            scores[index] = scores.get(index, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda index: (-scores[index], index))
