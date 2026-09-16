"""Query broker transformations: pure, deterministic, offline (no network)."""

import pytest

from artikel_mcp.query_broker import adapt

QUERY = "status hukum deepfake di indonesia"


def test_arxiv_drops_stopwords_and_expands_synonyms():
    # the arxiv adapter itself applies the `all:` field wrapper (arxiv.py),
    # so the broker returns the expanded keyword body only
    q = adapt(QUERY, "arxiv")
    assert "status" not in q and "di" not in q
    assert "hukum OR law OR legal" in q
    assert "deepfake" in q and "indonesia" in q


def test_english_indexes_get_expanded_keywords():
    for source in ("arxiv", "crossref", "doaj", "europepmc", "hal", "pmc"):
        assert adapt(QUERY, source) == "hukum OR law OR legal deepfake indonesia"


def test_garuda_keeps_raw_user_tokens():
    assert adapt(QUERY, "garuda") == QUERY


def test_unknown_source_rejected():
    with pytest.raises(ValueError, match="unsupported source: sinta"):
        adapt(QUERY, "sinta")


def test_deterministic_across_calls():
    for source in ("arxiv", "crossref", "doaj", "europepmc", "hal", "pmc", "garuda"):
        assert adapt(QUERY, source) == adapt(QUERY, source)


def test_offline_pure_function_no_side_effects():
    # result depends only on (source, query); repeatability across instances
    a = adapt(QUERY, "arxiv")
    for _ in range(10):
        assert adapt(QUERY, "arxiv") == a


def test_cross_source_isolation():
    arxiv_q = adapt(QUERY, "arxiv")
    garuda_q = adapt(QUERY, "garuda")
    assert arxiv_q != garuda_q
    assert arxiv_q == "hukum OR law OR legal deepfake indonesia"
    assert garuda_q == QUERY
