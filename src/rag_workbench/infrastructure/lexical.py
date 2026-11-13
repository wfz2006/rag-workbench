import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator

_CJK = re.compile(r"[\u3400-\u9fff]+")
_ASCII = re.compile(r"c\+\+|==|!=|<=|>=|->|\+\+|--|[a-z0-9_]+|\d+")
_DOI = re.compile(r"(?<![a-z0-9])10\.\d{4,9}/[a-z0-9][a-z0-9._;:/()-]*")
_COMPOUND = re.compile(
    r"(?<![a-z0-9_-])(?:[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\([a-z0-9]+\)\d*)?"
    r"|\d+[a-z][a-z0-9]+|[a-z]{2,}\d[a-z0-9]*)(?![a-z0-9_(-])"
)
_DASHES = str.maketrans("‐‑–—−", "-----")
_HYPHEN_WRAP = re.compile(r"(?<=[a-z0-9])-[ \t]*\n[ \t]*(?=[a-z0-9])")


def identifier_text(text: str) -> str:
    """Normalize spelling variants introduced by PDF extraction around identifiers."""
    return _HYPHEN_WRAP.sub("-", unicodedata.normalize("NFKC", text).casefold().translate(_DASHES))


def _identifiers(text: str) -> Iterator[re.Match[str]]:
    dois = list(_DOI.finditer(text))
    yield from dois
    for match in _COMPOUND.finditer(text):
        term = match[0]
        if not (any(char.isdigit() for char in term) or term.count("-") >= 2):
            continue
        if not any(doi.start() <= match.start() < doi.end() for doi in dois):
            yield match


def _identifier_term(value: str) -> str:
    value = value.rstrip(".;:")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def _mixed_query_terms(query: str) -> set[str]:
    """Use explicit identifiers and numeric constraints as a cross-language recall path."""
    text = identifier_text(query)
    if not _CJK.search(text):
        return set()
    matches = sorted(_identifiers(text), key=lambda match: match.start())
    if not matches:
        return set()
    terms = {_identifier_term(match[0]) for match in matches}
    for match in reversed(matches):
        text = text[:match.start()] + " " + text[match.end():]
    terms.update(token for token in _ASCII.findall(text) if token.isdecimal())
    return terms


def tokenize(text: str) -> list[str]:
    """Character unigrams/bigrams for Chinese, plus ASCII identifiers and numbers."""
    text = identifier_text(text)
    tokens: list[str] = []
    for run in _CJK.findall(text):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    tokens.extend(_ASCII.findall(text))
    tokens.extend(
        term
        for match in _identifiers(text)
        if _ASCII.fullmatch(term := _identifier_term(match[0])) is None
    )
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
        return self.coverage_any([query])

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

    def mixed_identifier_matches(self, query: str) -> list[bool]:
        terms = _mixed_query_terms(query)
        if not terms:
            return []
        return [terms.issubset(document) for document in self._documents]


def reciprocal_rank_fusion(rankings: list[list[int]], *, k: int = 60) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking, 1):
            scores[index] = scores.get(index, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda index: (-scores[index], index))
