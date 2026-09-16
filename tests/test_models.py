"""Tests for the normalized paper record model."""

from artikel_mcp.models import PaperRecord


def test_roundtrip_nullable_fields_unset():
    rec = PaperRecord(source="arxiv", source_id="2101.00001", title="A Paper")
    assert rec.dedup_key() == "arxiv:2101.00001"
    assert rec.doi is None
    assert rec.abstract is None
    assert rec.year is None
    assert rec.pdf_url is None


def test_dedup_key_prefers_doi():
    rec = PaperRecord(
        source="crossref", source_id="abc123", title="X", doi="10.1234/ABC.1"
    )
    assert rec.dedup_key().lower() == "10.1234/abc.1"