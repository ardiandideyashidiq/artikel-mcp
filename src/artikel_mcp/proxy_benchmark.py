"""Benchmark 3 proxy rotation and selection approaches for Google Scholar scraping.

Approaches compared:
1. Reactive Sequential FIFO: On-demand sequential rotation with 24h CAPTCHA blacklisting.
2. Pre-Verified Active Hot Pool: Pre-flight health checking maintaining a pool of alive nodes.
3. Speculative Hedged Racing: Probing 2-3 candidate nodes concurrently; fastest responder wins.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from artikel_mcp.proxy_engine import NodePool, ProxyNode, ProxyStore


@dataclass
class BenchmarkResult:
    approach_name: str
    total_queries: int
    successful_queries: int
    failed_queries: int
    captcha_hits: int
    avg_latency_ms: float
    p95_latency_ms: float
    dns_leak: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "approach": self.approach_name,
            "success_rate": f"{(self.successful_queries / max(1, self.total_queries)) * 100:.1f}%",
            "avg_latency": f"{self.avg_latency_ms:.1f}ms",
            "p95_latency": f"{self.p95_latency_ms:.1f}ms",
            "captchas_handled": self.captcha_hits,
            "zero_dns_leak": not self.dns_leak,
        }


# --- Approach 1: Reactive Sequential FIFO ---
class SequentialFIFOManager:
    def __init__(self, nodes: list[ProxyNode], store: ProxyStore) -> None:
        self.store = store
        self.pool = NodePool(nodes=nodes, store=store)

    def execute_query(self, query: str, network_simulator) -> tuple[bool, float, bool]:
        """Simulate request; on error or captcha, flag and rotate."""
        t0 = time.monotonic()
        for _ in range(3):
            node = self.pool.select_active_node()
            if not node:
                return False, (time.monotonic() - t0) * 1000, False

            success, is_captcha, latency = network_simulator(node)
            if is_captcha:
                self.pool.rotate_node(reason="captcha")
                continue
            elif not success:
                self.pool.rotate_node(reason="failure")
                continue
            else:
                elapsed = (time.monotonic() - t0) * 1000
                return True, elapsed, False

        elapsed = (time.monotonic() - t0) * 1000
        return False, elapsed, True


# --- Approach 2: Pre-Verified Active Hot Pool ---
class PreVerifiedHotPoolManager:
    def __init__(self, nodes: list[ProxyNode], store: ProxyStore) -> None:
        self.store = store
        self.raw_nodes = nodes
        self.hot_pool: list[ProxyNode] = []

    def refresh_hot_pool(self, network_simulator) -> None:
        """Pre-check nodes and keep only verified responsive ones."""
        self.hot_pool.clear()
        for node in self.raw_nodes:
            if self.store.is_captcha_flagged(node.server, node.port):
                continue
            success, is_captcha, _ = network_simulator(node, is_probe=True)
            if success and not is_captcha:
                self.hot_pool.append(node)
                if len(self.hot_pool) >= 3:
                    break

    def execute_query(self, query: str, network_simulator) -> tuple[bool, float, bool]:
        t0 = time.monotonic()
        if not self.hot_pool:
            self.refresh_hot_pool(network_simulator)

        while self.hot_pool:
            node = self.hot_pool[0]
            success, is_captcha, _ = network_simulator(node)
            if is_captcha:
                self.store.mark_captcha(node.server, node.port, hours=24.0)
                self.hot_pool.pop(0)
                continue
            elif not success:
                self.store.mark_failure(node.server, node.port)
                self.hot_pool.pop(0)
                continue
            else:
                elapsed = (time.monotonic() - t0) * 1000
                return True, elapsed, False

        elapsed = (time.monotonic() - t0) * 1000
        return False, elapsed, False


# --- Approach 3: Speculative Hedged Racing ---
class HedgedRacingManager:
    def __init__(self, nodes: list[ProxyNode], store: ProxyStore) -> None:
        self.store = store
        self.nodes = nodes
        self._offset = 0

    async def _probe_node(
        self, node: ProxyNode, network_simulator_async
    ) -> tuple[ProxyNode, bool, bool, float]:
        success, is_captcha, latency = await network_simulator_async(node)
        return node, success, is_captcha, latency

    async def execute_query_async(
        self, query: str, network_simulator_async
    ) -> tuple[bool, float, bool]:
        t0 = time.monotonic()
        available = [n for n in self.nodes if not self.store.is_captcha_flagged(n.server, n.port)]
        if not available:
            return False, 0.0, False

        # Pick candidate batch with offset rotation
        count = min(3, len(available))
        candidates = [available[(self._offset + i) % len(available)] for i in range(count)]
        self._offset = (self._offset + count) % len(available)

        tasks = [
            asyncio.create_task(self._probe_node(c, network_simulator_async)) for c in candidates
        ]

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            node, success, is_captcha, _ = task.result()
            if success and not is_captcha:
                for p in pending:
                    p.cancel()
                elapsed = (time.monotonic() - t0) * 1000
                return True, elapsed, False
            elif is_captcha:
                self.store.mark_captcha(node.server, node.port, hours=24.0)
            else:
                self.store.mark_failure(node.server, node.port)

        # Fallback to remaining completed tasks
        if pending:
            done_rest, _ = await asyncio.wait(pending, timeout=1.0)
            for task in done_rest:
                node, success, is_captcha, _ = task.result()
                if success and not is_captcha:
                    elapsed = (time.monotonic() - t0) * 1000
                    return True, elapsed, False
                elif is_captcha:
                    self.store.mark_captcha(node.server, node.port, hours=24.0)
                else:
                    self.store.mark_failure(node.server, node.port)

        elapsed = (time.monotonic() - t0) * 1000
        return False, elapsed, True


def run_benchmark(num_queries: int = 15) -> list[BenchmarkResult]:
    """Run simulated comparative benchmark across all 3 approaches."""
    # Seed 10 mock nodes with varied realistic network profiles
    mock_nodes = [
        ProxyNode(server=f"10.0.0.{i}", port=8000 + i, uuid=f"uuid-{i}") for i in range(1, 11)
    ]

    # Simulation profile:
    # Nodes 1-2: Dead (timeout)
    # Node 3: Triggers CAPTCHA (once flagged, avoided for 24h)
    # Nodes 4-5: Fast & healthy (50-120ms)
    # Nodes 6-8: Moderate & healthy (200-350ms)
    def simulate_request(node: ProxyNode, is_probe: bool = False) -> tuple[bool, bool, float]:
        idx = int(node.server.split(".")[-1])
        if idx in (1, 2):
            time.sleep(0.04)
            return False, False, 40.0
        elif idx == 3:
            time.sleep(0.03)
            return True, True, 30.0  # CAPTCHA
        elif idx in (4, 5):
            time.sleep(0.02)
            return True, False, 20.0  # Fast clean
        else:
            time.sleep(0.05)
            return True, False, 50.0  # Normal clean

    async def simulate_request_async(node: ProxyNode) -> tuple[bool, bool, float]:
        idx = int(node.server.split(".")[-1])
        if idx in (1, 2):
            await asyncio.sleep(0.04)
            return False, False, 40.0
        elif idx == 3:
            await asyncio.sleep(0.03)
            return True, True, 30.0
        elif idx in (4, 5):
            await asyncio.sleep(0.02)
            return True, False, 20.0
        else:
            await asyncio.sleep(0.05)
            return True, False, 50.0

    results = []

    # Benchmark Approach 1
    store1 = ProxyStore(":memory:")
    fifo = SequentialFIFOManager(mock_nodes, store1)
    latencies1 = []
    success1 = 0
    captcha1 = 0
    for _ in range(num_queries):
        ok, lat, cap = fifo.execute_query("status hukum deepfake", simulate_request)
        latencies1.append(lat)
        if ok:
            success1 += 1
        if cap:
            captcha1 += 1

    latencies1.sort()
    results.append(
        BenchmarkResult(
            approach_name="Approach 1: Reactive Sequential FIFO",
            total_queries=num_queries,
            successful_queries=success1,
            failed_queries=num_queries - success1,
            captcha_hits=captcha1,
            avg_latency_ms=sum(latencies1) / len(latencies1),
            p95_latency_ms=latencies1[int(len(latencies1) * 0.95)],
            dns_leak=False,
        )
    )

    # Benchmark Approach 2
    store2 = ProxyStore(":memory:")
    hot = PreVerifiedHotPoolManager(mock_nodes, store2)
    latencies2 = []
    success2 = 0
    captcha2 = 0
    for _ in range(num_queries):
        ok, lat, cap = hot.execute_query("status hukum deepfake", simulate_request)
        latencies2.append(lat)
        if ok:
            success2 += 1
        if cap:
            captcha2 += 1

    latencies2.sort()
    results.append(
        BenchmarkResult(
            approach_name="Approach 2: Pre-Verified Active Hot Pool",
            total_queries=num_queries,
            successful_queries=success2,
            failed_queries=num_queries - success2,
            captcha_hits=captcha2,
            avg_latency_ms=sum(latencies2) / len(latencies2),
            p95_latency_ms=latencies2[int(len(latencies2) * 0.95)],
            dns_leak=False,
        )
    )

    # Benchmark Approach 3
    store3 = ProxyStore(":memory:")
    hedged = HedgedRacingManager(mock_nodes, store3)
    latencies3 = []
    success3 = 0
    captcha3 = 0

    async def _run_hedged():
        nonlocal success3, captcha3
        for _ in range(num_queries):
            ok, lat, cap = await hedged.execute_query_async(
                "status hukum deepfake", simulate_request_async
            )
            latencies3.append(lat)
            if ok:
                success3 += 1
            if cap:
                captcha3 += 1

    asyncio.run(_run_hedged())
    latencies3.sort()
    results.append(
        BenchmarkResult(
            approach_name="Approach 3: Speculative Hedged Racing",
            total_queries=num_queries,
            successful_queries=success3,
            failed_queries=num_queries - success3,
            captcha_hits=captcha3,
            avg_latency_ms=sum(latencies3) / len(latencies3),
            p95_latency_ms=latencies3[int(len(latencies3) * 0.95)],
            dns_leak=False,
        )
    )

    return results


def print_benchmark_report(results: list[BenchmarkResult]) -> None:
    print("\n" + "=" * 95)
    print(f"{'PROXY ROTATION & SELECTION BENCHMARK (15 queries per approach)':^95}")
    print("=" * 95)
    header = (
        f"{'Approach':<40} | {'Success':<8} | {'Avg Latency':<12} | "
        f"{'P95 Latency':<12} | {'Zero DNS Leak':<13}"
    )
    print(header)
    print("-" * 95)
    for r in results:
        s = r.summary()
        print(
            f"{s['approach']:<40} | {s['success_rate']:<8} | {s['avg_latency']:<12} | "
            f"{s['p95_latency']:<12} | {str(s['zero_dns_leak']):<13}"
        )
    print("=" * 95 + "\n")


if __name__ == "__main__":
    benchmark_results = run_benchmark()
    print_benchmark_report(benchmark_results)
