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


def test_extract_query_limit_only_matches_paper_contexts():
    from artikel_mcp.query_broker import extract_query_limit

    # Non-paper numbers should NOT be extracted as limits (defaulting to 10)
    q1, lim1 = extract_query_limit("search 20 deep learning algorithms")
    assert lim1 is None
    assert "20" in q1

    q2, lim2 = extract_query_limit("find 15 methods for optimization")
    assert lim2 is None
    assert "15" in q2

    q3, lim3 = extract_query_limit("list 10 sorting techniques")
    assert lim3 is None

    # Legitimate paper count requests MUST be extracted
    q4, lim4 = extract_query_limit("find 20 papers on deep learning")
    assert lim4 == 20
    assert q4 == "deep learning"

    q5, lim5 = extract_query_limit("cari 15 artikel tentang AI")
    assert lim5 == 15
    assert q5 == "AI"

    q6, lim6 = extract_query_limit("tolong carikan 10 jurnal tentang hukum")
    assert lim6 == 10
    assert q6 == "hukum"

    q7, lim7 = extract_query_limit("find 10 on machine learning")
    assert lim7 == 10
    assert q7 == "machine learning"

    q8, lim8 = extract_query_limit("30 papers about quantum computing")
    assert lim8 == 30
    assert q8 == "quantum computing"

    q9, lim9 = extract_query_limit("deep learning sebanyak 75")
    assert lim9 == 75
    assert q9 == "deep learning"
