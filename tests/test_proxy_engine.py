"""Tests for custom Python SOCKS5 proxy engine and zero DNS leak guarantees."""

from __future__ import annotations

import socket
import struct

from artikel_mcp.proxy_engine import (
    LocalSocks5Server,
    NodePool,
    ProxyNode,
    build_vless_request_header,
    parse_vless_line,
)


def test_parse_vless_line_tcp_and_reality():
    line_reality = (
        "vless://user-id-123@91.107.185.25:38466"
        "?encryption=none&security=reality&sni=play.google.com&fp=chrome"
        "&pbk=o9QosLrFuqtMUXoPURDVw6JMr2Aw4e-ztIJoV-Li7gY&sid=2d26&spx=%2F&type=tcp#Node1"
    )
    node = parse_vless_line(line_reality)
    assert node is not None
    assert node.server == "91.107.185.25"
    assert node.port == 38466
    assert node.uuid == "user-id-123"
    assert node.network == "tcp"
    assert node.security == "reality"
    assert node.sni == "play.google.com"

    line_ws = (
        "vless://a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d@185.146.173.39:8080"
        "?path=%2Fws&security=tls&host=example.com&type=ws#Node2"
    )
    node_ws = parse_vless_line(line_ws)
    assert node_ws is not None
    assert node_ws.server == "185.146.173.39"
    assert node_ws.port == 8080
    assert node_ws.network == "ws"
    assert node_ws.security == "tls"
    assert node_ws.sni == "example.com"
    assert node_ws.path == "/ws"

    assert parse_vless_line("invalid://something") is None


def test_build_vless_request_header_domain_zero_dns_leak():
    target_host = "scholar.google.com"
    target_port = 443
    test_uuid = "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"

    header = build_vless_request_header(test_uuid, target_host, target_port, is_domain=True)

    # 1. Version must be 0x00
    assert header[0] == 0x00

    # 2. Addons len must be 0x00 (at offset 17)
    assert header[17] == 0x00

    # 3. Command must be 0x01 (TCP CONNECT, at offset 18)
    assert header[18] == 0x01

    # 4. Port must be 443 in big-endian (offset 19..21)
    port = struct.unpack("!H", header[19:21])[0]
    assert port == 443

    # 5. Address type must be 0x02 (Domain Name, ensuring remote DNS resolution)
    assert header[21] == 0x02

    # 6. Next byte is domain length followed by the raw domain string
    domain_len = header[22]
    assert domain_len == len(target_host)
    extracted_domain = header[23 : 23 + domain_len].decode("utf-8")
    assert extracted_domain == target_host


def test_node_pool_rotation():
    from artikel_mcp.proxy_engine import ProxyStore

    store = ProxyStore(":memory:")
    n1 = ProxyNode(server="1.1.1.1", port=443, uuid="id1")
    n2 = ProxyNode(server="2.2.2.2", port=443, uuid="id2")
    pool = NodePool([n1, n2], store=store)

    assert pool.select_active_node().endpoint == n1.endpoint
    assert pool.rotate_node().endpoint == n2.endpoint
    assert pool.select_active_node().endpoint == n2.endpoint
    assert pool.rotate_node().endpoint == n1.endpoint


def test_socks5_server_handshake_and_no_dns_leak(monkeypatch):
    from artikel_mcp.proxy_engine import ProxyStore

    test_port = 19808
    store = ProxyStore(":memory:")
    node = ProxyNode(server="127.0.0.1", port=19999, uuid="fake-uuid")
    server = LocalSocks5Server(
        host="127.0.0.1", port=test_port, node_pool=NodePool([node], store=store)
    )
    server.start()

    # Guard: Monkeypatch gethostbyname to guarantee that local DNS is NEVER called!
    def _forbidden_dns_lookup(host):
        if "scholar.google.com" in host:
            raise AssertionError(f"DNS LEAK DETECTED: {host} was queried locally!")
        return "127.0.0.1"

    monkeypatch.setattr(socket, "gethostbyname", _forbidden_dns_lookup)

    try:
        # Connect to SOCKS5 server as a client
        client_sock = socket.create_connection(("127.0.0.1", test_port), timeout=2.0)

        # 1. SOCKS5 Greeting: [Version 5, 1 Method, Method 0 (NO AUTH)]
        client_sock.sendall(b"\x05\x01\x00")
        greet_resp = client_sock.recv(2)
        assert greet_resp == b"\x05\x00"

        # 2. SOCKS5 CONNECT with ATYP 0x03 (Domain Name)
        target = b"scholar.google.com"
        port_bytes = struct.pack("!H", 443)
        connect_req = b"\x05\x01\x00\x03" + bytes([len(target)]) + target + port_bytes
        client_sock.sendall(connect_req)

        # Wait for SOCKS5 response (or failure to upstream node since upstream 19999 is closed)
        resp = client_sock.recv(10)
        assert len(resp) >= 2
        assert resp[0] == 0x05  # Version 5 response
        client_sock.close()
    finally:
        server.stop()


def test_proxy_store_captcha_24h_blacklist():
    from artikel_mcp.proxy_engine import ProxyStore

    store = ProxyStore(":memory:")
    node1 = ProxyNode(server="1.2.3.4", port=8080, uuid="u1")
    node2 = ProxyNode(server="5.6.7.8", port=8080, uuid="u2")
    store.upsert_node(node1)
    store.upsert_node(node2)

    # Initially both are healthy
    healthy = store.get_healthy_nodes()
    assert len(healthy) == 2

    # Flag node1 for CAPTCHA
    store.mark_captcha(node1.server, node1.port, hours=24.0)
    assert store.is_captcha_flagged(node1.server, node1.port) is True
    assert store.is_captcha_flagged(node2.server, node2.port) is False

    # Filtered healthy nodes must exclude node1
    healthy_after = store.get_healthy_nodes()
    assert len(healthy_after) == 1
    assert healthy_after[0].server == "5.6.7.8"


def test_benchmark_execution():
    from artikel_mcp.proxy_benchmark import run_benchmark

    results = run_benchmark(num_queries=3)
    assert len(results) == 3
    assert results[0].approach_name.startswith("Approach 1")
    assert results[1].approach_name.startswith("Approach 2")
    assert results[2].approach_name.startswith("Approach 3")
    for r in results:
        assert r.dns_leak is False
