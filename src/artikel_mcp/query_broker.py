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
    "status",
    "di",
    "yang",
    "dan",
    "dari",
    "tentang",
    "pada",
    "untuk",
    "dengan",
    "dalam",
    "sebuah",
    "ke",
    "oleh",
    "ini",
    "itu",
    "atau",
    "sebagai",
    "the",
    "of",
    "and",
    "a",
    "an",
    "in",
    "on",
    "for",
    "about",
    "with",
    "by",
    "at",
    "from",
    "as",
    "to",
    "is",
    "are",
    "was",
    "were",
}

# Multi-domain Indonesian -> English synonym expansion, applied as "id OR en1 OR en2".
_SYNONYMS = {
    # Legal & governance
    "hukum": "hukum OR law OR legal",
    "pidana": "pidana OR criminal",
    "perlindungan": "perlindungan OR protection",
    "privasi": "privasi OR privacy",
    "kejahatan": "kejahatan OR crime",
    "penyebaran": "penyebaran OR dissemination",
    "penipuan": "penipuan OR fraud",
    "pornografi": "pornografi OR pornography",
    # Computer science, AI & data
    "kecerdasan": "kecerdasan OR intelligence",
    "buatan": "buatan OR artificial",
    "pembelajaran": "pembelajaran OR learning",
    "mesin": "mesin OR machine",
    "jaringan": "jaringan OR network",
    "citra": "citra OR image",
    "pengenalan": "pengenalan OR recognition",
    "keamanan": "keamanan OR security",
    # Health, science & environment
    "kesehatan": "kesehatan OR health OR healthcare",
    "medis": "medis OR medical",
    "penyakit": "penyakit OR disease",
    "deteksi": "deteksi OR detection",
    "pertanian": "pertanian OR agriculture",
    "lingkungan": "lingkungan OR environment",
    "pendidikan": "pendidikan OR education",
    "ekonomi": "ekonomi OR economy OR economic",
}

_TOKEN_RE = re.compile(r"\w+")

_CONVERSATIONAL_PREFIX_RE = re.compile(
    r"^(?:"
    r"(?:tolong\s+)?(?:carikan|cari)\s+(?:beberapa\s+)?(?:jurnal|artikel|paper|penelitian|studi)?\s*(?:tentang|mengenai|terkait|soal)?|"
    r"(?:jurnal|artikel|paper|penelitian|studi)\s+(?:tentang|mengenai|terkait)|"
    r"(?:please\s+)?(?:find|search(?:\s+for)?|look\s+up)\s+(?:papers|articles|research|literature|studies)?\s*(?:about|on|regarding)?|"
    r"(?:papers|articles|research|literature)\s+(?:about|on|regarding)"
    r")\s+",
    re.IGNORECASE,
)

_DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.IGNORECASE
)
_ARXIV_ID_RE = re.compile(
    r"(?:https?://arxiv\.org/(?:abs|pdf)/)?(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)


def clean_conversational_query(query: str) -> str:
    """Strip conversational filler like 'tolong carikan jurnal tentang'."""
    q = query.strip()
    q = _CONVERSATIONAL_PREFIX_RE.sub("", q).strip()
    # strip wrapping quotes or trailing question marks
    q = re.sub(r'^[“"\'`]+|[”"\'`?]+$', "", q).strip()
    return q or query.strip()


def extract_identifier(query: str) -> dict[str, str] | None:
    """Check if query directly specifies a DOI or arXiv identifier."""
    stripped = query.strip()
    doi_m = _DOI_RE.search(stripped)
    if doi_m:
        return {"type": "doi", "value": doi_m.group(1)}
    arxiv_m = _ARXIV_ID_RE.search(stripped)
    if arxiv_m:
        return {"type": "arxiv", "value": arxiv_m.group(1)}
    return None


def _expand(token: str) -> str:
    return _SYNONYMS.get(token, token)


def _expanded_query(query: str) -> str:
    """Strip stopwords and expand synonyms to an id/en-safe keyword string."""
    cleaned = clean_conversational_query(query)
    tokens = [t for t in _TOKEN_RE.findall(cleaned.lower()) if t not in _STOPWORDS]
    if not tokens:
        return cleaned
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
    # garuda: Indonesian corpus; keep user's search keywords without filler
    "garuda": clean_conversational_query,
}


def local_adapt(query: str) -> str:
    """Return the broker-adapted keyword string for the local FTS cache.

    Same stopword/synonym transformation as the arxiv path, without
    requiring a source name. The consumer (PaperCache.search with
    adapted=True) prefixes each token for FTS5 matching.
    """
    return _expanded_query(query)


def adapt(query: str, source: str) -> str:
    """Return the query string a given source should receive.

    Unsupported sources raise ValueError so a typo'd source name never
    silently falls back to raw identity.
    """
    transform = _TRANSFORMS.get(source)
    if transform is None:
        raise ValueError(f"unsupported source: {source}")
    return transform(query)
