"""Offline tests for BibTeX ingestion (no network)."""

import pytest

from artikel_mcp.bib import BibFileError, collect_parse_errors, parse_bib_file
from artikel_mcp.cache import PaperCache
from artikel_mcp.service import get_cached_paper, ingest_bibliography

# Fixture reproducing the real-world quirks of a Zotero-style export:
# Korean fields, '#' and quotes in titles, '--' page ranges, 'and'-joined
# authors, an entry missing DOI/abstract, a duplicate DOI, and a malformed
# block that must not abort the rest.
BIB = r"""
@article{Jung2025grassroots,
  title = {From grassroots advocacy to 'AI governance': lessons from South Korea},
  author = {Sohee Jung and Hyeseon Noh},
  journal = {International Journal of Comparative and Applied Criminal Justice},
  year = {2025},
  volume = {50},
  number = {3},
  pages = {299--336},
  doi = {10.1080/01924036.2025.2596578},
  url = {https://doi.org/10.1080/01924036.2025.2596578},
  abstract = {This paper explores the 2024 deepfake crisis. The findings show
              grassroots advocacy reshaped policy.}
}

@article{Ji2025metoo,
  title = {\#MeToo in an AI-generated deepfake sexual violence era},
  author = {SeungGyeong Ji},
  journal = {Women's Studies International Forum},
  year = {2025},
  volume = {112},
  pages = {103146},
  doi = {10.1016/j.wsif.2025.103146}
}

@article{Kim2024korean,
  title = {딥페이크 성범죄 대응 정책},
  author = {김수아 and 여성학협동과정 여성연구소},
  journal = {이화젠더법학},
  year = {2024},
  pages = {187--224},
  url = {https://scholar.kyobobook.co.kr/article/detail/4040070792982}
}

@article{Jung2025grassroots2,
  title = {From grassroots advocacy to 'AI governance': lessons from South Korea},
  author = {Sohee Jung and Hyeseon Noh},
  journal = {International Journal of Comparative and Applied Criminal Justice},
  year = {2025},
  doi = {10.1080/01924036.2025.2596578}
}

@article{broken1,
  title = {This block never closes,
  author = {Nobody Here},
  year = {2021}

@article{notitle,
  author = {No Title Person},
  journal = {Somewhere},
  year = {2020}
}
"""


@pytest.fixture()
def bib_file(tmp_path):
    path = tmp_path / "refs.bib"
    path.write_text(BIB, encoding="utf-8")
    return path


def test_parse_valid_file_returns_normalized_records(bib_file):
    records = parse_bib_file(bib_file)
    by_id = {r.source_id: r for r in records}

    grass = by_id["Jung2025grassroots"]
    assert grass.source == "bib"
    assert grass.authors == ["Sohee Jung", "Hyeseon Noh"]
    assert grass.doi == "10.1080/01924036.2025.2596578"
    assert grass.year == 2025
    assert grass.publication == "International Journal of Comparative and Applied Criminal Justice"
    assert grass.extra["pages"] == "299--336"
    assert grass.extra["volume"] == "50"
    assert grass.url == "https://doi.org/10.1080/01924036.2025.2596578"

    korean = by_id["Kim2024korean"]
    assert korean.authors == ["김수아", "여성학협동과정 여성연구소"]
    assert korean.publication == "이화젠더법학"
    assert "#" in by_id["Ji2025metoo"].title


def test_missing_file_raises_bibfileerror(tmp_path):
    with pytest.raises(BibFileError):
        parse_bib_file(tmp_path / "does-not-exist.bib")


def test_missing_doi_and_abstract_entry_is_kept(bib_file):
    records = parse_bib_file(bib_file)
    korean = next(r for r in records if r.source_id == "Kim2024korean")
    assert korean.doi is None
    assert korean.abstract is None
    assert korean.research_results  # generated summary, not empty
    assert korean.url  # explicit url preserved


def test_duplicate_doi_shares_dedup_key(bib_file):
    records = parse_bib_file(bib_file)
    keys = [r.dedup_key() for r in records if r.doi == "10.1080/01924036.2025.2596578"]
    assert keys == ["10.1080/01924036.2025.2596578"] * 2


def test_malformed_and_titless_entries_skipped_without_aborting(bib_file):
    with collect_parse_errors() as errors:
        records = parse_bib_file(bib_file)
    ids = {r.source_id for r in records}
    # good entries survive around the malformed block
    assert {"Jung2025grassroots", "Ji2025metoo", "Kim2024korean"} <= ids
    # the malformed block and the title-less entry are gone
    assert "broken1" not in ids
    assert "notitle" not in ids
    # the malformed block is reported via the captured parser warnings
    assert errors


SERVICE_BIB = r"""
@article{alpha,
  title = {Alpha Deepfake Study},
  author = {Ada Lovelace},
  journal = {Journal of Tests},
  year = {2025},
  doi = {10.1/aaa},
  abstract = {The results show alpha outperforms baselines.}
}

@article{alpha-dup,
  title = {Alpha Deepfake Study},
  author = {Ada Lovelace},
  journal = {Journal of Tests},
  year = {2025},
  doi = {10.1/aaa}
}

@article{beta,
  title = {Beta Report Without a DOI},
  author = {Grace Hopper},
  journal = {Test Proceedings},
  year = {2024},
  url = {https://example.org/article/view/1}
}
"""


@pytest.fixture()
def cache(tmp_path):
    c = PaperCache(tmp_path / "bib.db")
    yield c
    c.close()


@pytest.fixture()
def service_bib(tmp_path):
    path = tmp_path / "service.bib"
    path.write_text(SERVICE_BIB, encoding="utf-8")
    return path


def _fake_download_using_cache(cache, calls):
    """Mirror download_paper's cached-markdown short-circuit without network."""

    def fake(cache_, *, doi=None, url=None, **kwargs):
        key = (doi or url or "").lower()
        existing = cache_.get_by_key(key)
        if existing and existing.markdown:
            calls.append("cached")
            return {"markdown": existing.markdown, "from_cache": True}
        calls.append("fresh")
        return {"markdown": f"# markdown for {key}", "doi": doi}

    return fake


def test_ingest_indexes_and_dedupes_doi(cache, service_bib):
    result = ingest_bibliography(cache, str(service_bib), download=False)
    assert result["count"] == 3
    rows = cache._conn.execute("SELECT count(*) FROM papers").fetchone()[0]
    assert rows == 2  # duplicate DOI collapsed
    assert cache.get_by_key("10.1/aaa") is not None


def test_download_disabled_skips_all(cache, service_bib, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "artikel_mcp.service.download_paper", _fake_download_using_cache(cache, calls)
    )
    result = ingest_bibliography(cache, str(service_bib), download=False)
    assert calls == []
    assert result["summary"]["skipped"] == 3
    assert all(r["status"] == "skipped" for r in result["records"])
    assert not any(r["has_full_text"] for r in result["records"])


def test_download_enabled_is_idempotent(cache, service_bib, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "artikel_mcp.service.download_paper", _fake_download_using_cache(cache, calls)
    )
    first = ingest_bibliography(cache, str(service_bib), download=True)
    assert first["summary"]["downloaded"] >= 1
    assert cache.get_by_key("10.1/aaa").markdown

    second = ingest_bibliography(cache, str(service_bib), download=True)
    assert second["summary"]["cached"] == second["count"]
    assert second["summary"]["downloaded"] == 0
    # no fresh download on the second pass: all entries already carry markdown
    assert calls.count("fresh") == first["summary"]["downloaded"]


def test_download_failure_isolated_per_entry(cache, service_bib, monkeypatch):
    def failing(cache_, *, doi=None, url=None, **kwargs):
        if doi is None:
            raise RuntimeError("no open-access copy")
        return {"markdown": f"# {doi}", "doi": doi}

    monkeypatch.setattr("artikel_mcp.service.download_paper", failing)
    result = ingest_bibliography(cache, str(service_bib), download=True)
    statuses = {r["source_id"]: r["status"] for r in result["records"]}
    assert statuses["alpha"] == "downloaded"
    assert statuses["alpha-dup"] == "cached"  # shares DOI with alpha
    assert statuses["beta"] == "failed"
    beta = next(r for r in result["records"] if r["source_id"] == "beta")
    assert beta["error"] == "no open-access copy"
    assert result["summary"]["failed"] == 1


def test_mcp_tool_ingest_bibliography_registered_and_callable(tmp_path):
    import asyncio

    from artikel_mcp.server import create_server

    bib = tmp_path / "tool.bib"
    bib.write_text(SERVICE_BIB, encoding="utf-8")
    srv = create_server(db_path=str(tmp_path / "server.db"))

    async def run():
        tools = await srv.list_tools()
        res = await srv.call_tool("ingest_bibliography", {"bib_path": str(bib), "download": False})
        return {t.name for t in tools}, res

    names, res = asyncio.run(run())
    assert "ingest_bibliography" in names
    assert res.is_error is False


NETWORK_BIB = r"""
@article{plos2022open,
  title = {A real open-access article for live ingest},
  author = {Live Test Author},
  journal = {PLOS ONE},
  year = {2022},
  doi = {10.1371/journal.pone.0266947}
}
"""


@pytest.mark.network
def test_live_ingest_downloads_and_persists(tmp_path):
    cache = PaperCache(tmp_path / "live_bib.db")
    path = tmp_path / "live.bib"
    path.write_text(NETWORK_BIB, encoding="utf-8")
    try:
        result = ingest_bibliography(cache, str(path), download=True)
        assert result["summary"]["downloaded"] == 1
        assert result["records"][0]["status"] == "downloaded"
        assert result["records"][0]["has_full_text"] is True

        cached = get_cached_paper(cache, "10.1371/journal.pone.0266947")
        assert cached is not None
        assert cached["markdown"]
    finally:
        cache.close()
