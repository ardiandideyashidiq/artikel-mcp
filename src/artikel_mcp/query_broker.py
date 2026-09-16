"""Source-adapted query broker.

Transforms a single user query into one query per academic source so each
upstream index receives a string tuned to its own syntax and language
affinity. Pure and deterministic: no network, no state, same output for the
same (source, query) input.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# ponytail: static stopword/synonym tables; text tweaks are data changes,
#           not code changes. Retrofit NLP-grade expansion if recall is still
#           weak after real-world queries.

# Indonesian + English low-information words, pruned before expansion.
_STOPWORDS = {
    "status", "di", "yang", "dan", "dari", "tentang", "pada", "untuk",
    "dengan", "dalam", "sebuah", "the", "of", "and", "a", "an", "in", "on",
    "for", "about", "with",
}

# Indonesian -> English synonym expansion, applied as "id OR en1 OR en2".
_SYNONYMS = {
    "hukum": "hukum OR law OR legal",
    "pidana": "pidana OR criminal",
    "perlindungan": "perlindungan OR protection",
    "privasi": "privasi OR privacy",
    "kejahatan": "kejahatan OR crime",
    "penyebaran": "penyebaran OR dissemination",
    "penipuan": "penipuan OR fraud",
    "pornografi": "pornografi OR pornography",
}

_TOKEN_RE = re.compile(r"\w+")


def _expand(token: str) -> str:
    return _SYNONYMS.get(token, token)


def _expanded_query(query: str) -> str:
    """Strip stopwords and expand synonyms to an id/en-safe keyword string."""
    tokens = [t for t in _TOKEN_RE.findall(query.lower()) if t not in _STOPWORDS]
    if not tokens:
        return query
    return " ".join(_expand(t) for t in tokens)


_TRANSFORMS: dict[str, Callable[[str], str]] = {
    # arxiv: adapter already prefixes the query with `all:` -- broker's job
    #        is token expansion, not the field wrapper.
    "arxiv": _expanded_query,
    # doaj/crossref: keyword search across title/abstract, English index
    "doaj": _expanded_query,
    "crossref": _expanded_query,
    "europepmc": _expanded_query,
    "hal": _expanded_query,
    "pmc": _expanded_query,
    # garuda: Indonesian corpus; keep the user's raw tokens
    "garuda": lambda q: q,
}


def adapt(query: str, source: str) -> str:
    """Return the query string a given source should receive.

    Unsupported sources raise ValueError so a typo'd source name never
    silently falls back to raw identity.
    """
    transform = _TRANSFORMS.get(source)
    if transform is None:
        raise ValueError(f"unsupported source: {source}")
    return transform(query)