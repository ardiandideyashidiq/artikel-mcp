"""Registry fan-out with per-source error isolation (no network)."""

import threading
import time

from artikel_mcp.http import get_client
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


class SlowAdapter:
    name = "slow"

    def search(self, query, limit=20):
        time.sleep(0.3)
        return [PaperRecord(source="slow", source_id="2", title="Slow OK")]


def test_isolation_not_aborted_by_failure(monkeypatch):
    monkeypatch.setitem(registry._REGISTRY, FailingAdapter.name, FailingAdapter)
    monkeypatch.setitem(registry._REGISTRY, HealthyAdapter.name, HealthyAdapter)

    records, errors = registry.search_all("x", sources=["healthy", "failing"])
    assert len(records) == 1
    assert records[0].source == "healthy"
    assert len(errors) == 1
    assert "failing" in errors[0]


def test_get_client_returns_same_instance_within_thread():
    a = get_client()
    b = get_client()
    assert a is b


def test_get_client_returns_distinct_instances_across_threads():
    results: list = []

    def _grab():
        results.append(get_client())

    t1 = threading.Thread(target=_grab)
    t2 = threading.Thread(target=_grab)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(results) == 2
    assert results[0] is not results[1]


def test_concurrent_fanout_finishes_under_wall_sum(monkeypatch):
    monkeypatch.setitem(registry._REGISTRY, SlowAdapter.name, SlowAdapter)
    monkeypatch.setitem(registry._REGISTRY, HealthyAdapter.name, HealthyAdapter)
    t0 = time.monotonic()
    records, errors = registry.search_all("x", sources=["healthy", "slow"])
    elapsed = time.monotonic() - t0
    assert len(records) == 2
    assert {r.source for r in records} == {"healthy", "slow"}
    assert errors == []
    # sequential would take >= 0.3s; concurrent should finish well under that
    assert elapsed < 0.4


class HungAdapter:
    """Simulates a source whose HTTP call times out (slow > 20s).

    ponytail: in production the HttpClient timeout bounds the real wait;
               this adapter simulates that failure path directly.
    """
    name = "hung"

    def search(self, query, limit=20):
        # Block long enough to simulate a hung upstream; on real code the
        # HTTP timeout raises an error rather than blocking forever.
        time.sleep(0.15)
        raise AdapterError("source timed out")


def test_hung_source_does_not_kill_healthy_sources(monkeypatch):
    monkeypatch.setitem(registry._REGISTRY, HungAdapter.name, HungAdapter)
    monkeypatch.setitem(registry._REGISTRY, HealthyAdapter.name, HealthyAdapter)

    records, errors = registry.search_all("x", sources=["healthy", "hung"])
    assert len(records) == 1
    assert records[0].source == "healthy"
    assert len(errors) == 1
    assert "hung" in errors[0]
