"""Registry fan-out with per-source error isolation (no network)."""


from artikel_mcp.models import PaperRecord
from artikel_mcp.sources import registry
from artikel_mcp.sources.base import AdapterError


class FailingAdapter:
    name = "failing"

    def search(self, query, limit=20):
        raise AdapterError("simulated outage")


class HealthyAdapter:
    name = "healthy"

    def search(self, query, limit=20):
        return [PaperRecord(source="healthy", source_id="1", title="OK")]


def test_isolation_not_aborted_by_failure(monkeypatch):
    monkeypatch.setitem(registry._REGISTRY, FailingAdapter.name, FailingAdapter)
    monkeypatch.setitem(registry._REGISTRY, HealthyAdapter.name, HealthyAdapter)

    records, errors = registry.search_all("x", sources=["healthy", "failing"])
    assert len(records) == 1
    assert records[0].source == "healthy"
    assert len(errors) == 1
    assert "failing" in errors[0]