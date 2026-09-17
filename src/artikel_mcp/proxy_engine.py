"""Custom Python SOCKS5 proxy engine with zero DNS leak and 24h CAPTCHA blacklisting.

Parses VLESS configurations (IP, port, UUID, protocol parameters),
persists proxy health states in SQLite, automatically blacklists nodes
flagged for CAPTCHA for 24 hours, and runs an in-process local SOCKS5 server (127.0.0.1:10808)
that forwards target connections to upstream VLESS nodes without local DNS resolution.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import sqlite3
import ssl
import struct
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("artikel_mcp.proxy_engine")

DEFAULT_VLESS_URL = os.getenv("ARTIKEL_MCP_VLESS_URL", "")


@dataclass
class ProxyNode:
    """Represents an extracted proxy node from a VLESS configuration line."""

    server: str
    port: int
    uuid: str
    network: str = "tcp"  # tcp, ws, etc.
    security: str = "none"  # none, tls, reality
    sni: str | None = None
    path: str = "/"
    host: str | None = None
    last_checked: float = 0.0
    latency_ms: float = 9999.0
    is_alive: bool = False

    @property
    def endpoint(self) -> str:
        return f"{self.server}:{self.port}"


def parse_vless_line(line: str) -> ProxyNode | None:
    """Parse a single vless:// URI into a ProxyNode instance."""
    clean = line.strip()
    if not clean.startswith("vless://"):
        return None
    try:
        raw_uri = clean.split("#")[0].strip()
        parsed = urllib.parse.urlparse(raw_uri)
        node_uuid = urllib.parse.unquote(parsed.username or "")
        server = parsed.hostname
        port = parsed.port
        if not server or not port:
            return None

        query_str = parsed.query.replace("&amp;", "&")
        params = urllib.parse.parse_qs(query_str)

        def get_p(key: str, default: str = "") -> str:
            vals = params.get(key)
            return vals[0] if vals else default

        net_type = get_p("type", "tcp").lower()
        security = get_p("security", "none").lower()
        sni = get_p("sni") or get_p("host") or None
        path = get_p("path", "/")
        host_hdr = get_p("host") or None

        return ProxyNode(
            server=server,
            port=int(port),
            uuid=node_uuid,
            network=net_type,
            security=security,
            sni=sni,
            path=path,
            host=host_hdr,
        )
    except Exception as e:
        logger.debug("Failed to parse vless line: %s", e)
        return None


def build_vless_request_header(
    node_uuid: str,
    target_addr: str,
    target_port: int,
    is_domain: bool = True,
    command: int = 1,  # 1 = TCP CONNECT
) -> bytes:
    """Construct VLESS request protocol header.

    Structure:
    - 1 byte: Protocol Version (0x00)
    - 16 bytes: User UUID (binary decoded)
    - 1 byte: Addons length (0x00)
    - 1 byte: Command (0x01 = TCP)
    - 2 bytes: Port (Big Endian)
    - 1 byte: Address type (0x01 = IPv4, 0x02 = Domain, 0x03 = IPv6)
    - Address bytes (if Domain: 1 byte length + ASCII bytes)
    """
    ver = b"\x00"
    try:
        raw_uuid = uuid.UUID(node_uuid).bytes
    except ValueError:
        raw_uuid = node_uuid.encode("utf-8")[:16].ljust(16, b"\x00")

    addons = b"\x00"
    cmd = bytes([command])
    port_bytes = struct.pack("!H", target_port)

    if is_domain:
        # Address Type 0x02: Domain Name (remote DNS resolution - zero DNS leak!)
        domain_bytes = target_addr.encode("utf-8")
        addr_payload = bytes([0x02, len(domain_bytes)]) + domain_bytes
    else:
        try:
            ip_bytes = socket.inet_aton(target_addr)
            addr_payload = bytes([0x01]) + ip_bytes
        except OSError:
            domain_bytes = target_addr.encode("utf-8")
            addr_payload = bytes([0x02, len(domain_bytes)]) + domain_bytes

    return ver + raw_uuid + addons + cmd + port_bytes + addr_payload


class ProxyStore:
    """Persistent SQLite store for proxy nodes, failure metrics, and 24h CAPTCHA blacklists."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            env = os.environ.get("ARTIKEL_MCP_PROXY_DB")
            if env:
                self.db_path = Path(env)
            else:
                base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
                self.db_path = base / "artikel-mcp" / "proxies.db"
        elif str(db_path) == ":memory:":
            self.db_path = Path(":memory:")
        else:
            self.db_path = Path(db_path)

        self._mem_conn: sqlite3.Connection | None = None
        if str(self.db_path) == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS proxy_nodes (
                    endpoint      TEXT PRIMARY KEY,
                    server        TEXT NOT NULL,
                    port          INTEGER NOT NULL,
                    uuid          TEXT NOT NULL,
                    network       TEXT NOT NULL,
                    security      TEXT NOT NULL,
                    sni           TEXT,
                    path          TEXT,
                    host          TEXT,
                    status        TEXT NOT NULL DEFAULT 'active',
                    captcha_until REAL DEFAULT 0.0,
                    fail_count    INTEGER DEFAULT 0,
                    latency_ms    REAL DEFAULT 9999.0,
                    last_used     REAL DEFAULT 0.0
                );
                """
            )
            conn.commit()

    def upsert_node(self, node: ProxyNode) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO proxy_nodes (
                    endpoint, server, port, uuid, network, security, sni, path, host
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    uuid=excluded.uuid,
                    network=excluded.network,
                    security=excluded.security,
                    sni=excluded.sni,
                    path=excluded.path,
                    host=excluded.host;
                """,
                (
                    node.endpoint,
                    node.server,
                    node.port,
                    node.uuid,
                    node.network,
                    node.security,
                    node.sni,
                    node.path,
                    node.host,
                ),
            )
            conn.commit()

    def mark_captcha(self, server: str, port: int, hours: float = 24.0) -> None:
        """Flag node as CAPTCHA-blocked for the next N hours (default 24h)."""
        until = time.time() + (hours * 3600.0)
        endpoint = f"{server}:{port}"
        logger.warning(
            "Proxy %s flagged for CAPTCHA until %.0f (for %.1fh)", endpoint, until, hours
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE proxy_nodes
                SET status = 'captcha', captcha_until = ?, fail_count = fail_count + 1
                WHERE endpoint = ?;
                """,
                (until, endpoint),
            )
            conn.commit()

    def mark_failure(self, server: str, port: int) -> None:
        endpoint = f"{server}:{port}"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE proxy_nodes
                SET fail_count = fail_count + 1, status = 'failed', last_used = ?
                WHERE endpoint = ?;
                """,
                (time.time(), endpoint),
            )
            conn.commit()

    def mark_success(self, server: str, port: int, latency_ms: float = 0.0) -> None:
        endpoint = f"{server}:{port}"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE proxy_nodes
                SET fail_count = 0, status = 'active', latency_ms = ?, last_used = ?
                WHERE endpoint = ?;
                """,
                (latency_ms, time.time(), endpoint),
            )
            conn.commit()

    def is_captcha_flagged(self, server: str, port: int) -> bool:
        endpoint = f"{server}:{port}"
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT captcha_until FROM proxy_nodes WHERE endpoint = ?;",
                (endpoint,),
            ).fetchone()
            if row and row["captcha_until"] > now:
                return True
        return False

    def get_healthy_nodes(self, limit: int = 50) -> list[ProxyNode]:
        """Fetch nodes not flagged for CAPTCHA, sorted by lowest latency and least recently used."""
        now = time.time()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT server, port, uuid, network, security, sni, path, host, latency_ms
                FROM proxy_nodes
                WHERE captcha_until <= ? AND fail_count < 5
                ORDER BY latency_ms ASC, last_used ASC
                LIMIT ?;
                """,
                (now, limit),
            ).fetchall()

            return [
                ProxyNode(
                    server=r["server"],
                    port=r["port"],
                    uuid=r["uuid"],
                    network=r["network"],
                    security=r["security"],
                    sni=r["sni"],
                    path=r["path"] or "/",
                    host=r["host"],
                    latency_ms=r["latency_ms"],
                )
                for r in rows
            ]


class NodePool:
    """Manages candidate proxy nodes with 24h CAPTCHA blacklisting and rotation."""

    def __init__(
        self,
        nodes: list[ProxyNode] | None = None,
        store: ProxyStore | None = None,
    ) -> None:
        self.store = store or ProxyStore()
        self._lock = threading.Lock()
        self._memory_nodes: list[ProxyNode] = []
        self._active_node: ProxyNode | None = None
        if nodes:
            for n in nodes:
                self.add_node(n)

    def add_node(self, node: ProxyNode) -> None:
        with self._lock:
            self._memory_nodes.append(node)
            self.store.upsert_node(node)

    def select_active_node(self) -> ProxyNode | None:
        """Select the current active node, or rotate if currently flagged."""
        with self._lock:
            # If current active node is still good and not flagged, keep using it
            if self._active_node and not self.store.is_captcha_flagged(
                self._active_node.server, self._active_node.port
            ):
                return self._active_node

            healthy = self.store.get_healthy_nodes(limit=10)
            if healthy:
                self._active_node = healthy[0]
                return self._active_node

            # Fallback to unflagged memory nodes
            for n in self._memory_nodes:
                if not self.store.is_captcha_flagged(n.server, n.port):
                    self._active_node = n
                    return n
            return None

    def rotate_node(self, reason: str = "failure") -> ProxyNode | None:
        """Rotate to next available node and mark current node status."""
        with self._lock:
            prev_endpoint = None
            if self._active_node:
                prev_endpoint = self._active_node.endpoint
                if reason == "captcha":
                    self.store.mark_captcha(
                        self._active_node.server, self._active_node.port, hours=24.0
                    )
                else:
                    self.store.mark_failure(self._active_node.server, self._active_node.port)
            self._active_node = None

            healthy = self.store.get_healthy_nodes(limit=10)
            candidates = [h for h in healthy if h.endpoint != prev_endpoint]
            if candidates:
                self._active_node = candidates[0]
                return self._active_node
            if healthy:
                self._active_node = healthy[0]
                return self._active_node

            for n in self._memory_nodes:
                if n.endpoint != prev_endpoint and not self.store.is_captcha_flagged(
                    n.server, n.port
                ):
                    self._active_node = n
                    return n

            return None


class LocalSocks5Server:
    """Pure-Python asynchronous SOCKS5 proxy server.

    Listens on 127.0.0.1 and relays connections to an upstream VLESS node.
    Enforces strict remote domain resolution for zero DNS leaks.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 10808,
        node_pool: NodePool | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.node_pool = node_pool or NodePool()
        self._server: asyncio.Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    async def _handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            greet = await client_reader.read(2)
            if len(greet) < 2 or greet[0] != 0x05:
                client_writer.close()
                return
            nmethods = greet[1]
            methods = await client_reader.read(nmethods)
            if 0x00 not in methods:  # NO AUTH
                client_writer.write(b"\x05\xff")
                await client_writer.drain()
                client_writer.close()
                return

            client_writer.write(b"\x05\x00")
            await client_writer.drain()

            req = await client_reader.read(4)
            if len(req) < 4 or req[0] != 0x05 or req[1] != 0x01:  # CONNECT
                client_writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                await client_writer.drain()
                client_writer.close()
                return

            atyp = req[3]
            target_host = ""
            is_domain = False

            if atyp == 0x01:  # IPv4
                raw_ip = await client_reader.read(4)
                target_host = socket.inet_ntoa(raw_ip)
                is_domain = False
            elif atyp == 0x03:  # Domain (STRICT ZERO DNS LEAK: do NOT resolve locally!)
                len_byte = await client_reader.read(1)
                domain_len = len_byte[0]
                domain_bytes = await client_reader.read(domain_len)
                target_host = domain_bytes.decode("utf-8", "replace")
                is_domain = True
            elif atyp == 0x04:  # IPv6
                raw_ip = await client_reader.read(16)
                target_host = socket.inet_ntop(socket.AF_INET6, raw_ip)
                is_domain = False
            else:
                client_writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                await client_writer.drain()
                client_writer.close()
                return

            raw_port = await client_reader.read(2)
            target_port = struct.unpack("!H", raw_port)[0]

            node = self.node_pool.select_active_node()
            if not node:
                client_writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
                await client_writer.drain()
                client_writer.close()
                return

            try:
                ssl_ctx = None
                if node.security in ("tls", "reality"):
                    ssl_ctx = ssl.create_default_context()
                    insecure = os.getenv("ARTIKEL_MCP_PROXY_INSECURE_TLS", "0") == "1"
                    if insecure:
                        ssl_ctx.check_hostname = False
                        ssl_ctx.verify_mode = ssl.CERT_NONE
                    else:
                        ssl_ctx.check_hostname = bool(node.sni)
                        ssl_ctx.verify_mode = ssl.CERT_REQUIRED

                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        node.server,
                        node.port,
                        ssl=ssl_ctx,
                        server_hostname=node.sni if ssl_ctx else None,
                    ),
                    timeout=8.0,
                )
            except Exception as e:
                logger.warning(
                    "Failed to connect to upstream node %s:%d: %s",
                    node.server,
                    node.port,
                    e,
                )
                self.node_pool.rotate_node(reason="failure")
                client_writer.write(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
                await client_writer.drain()
                client_writer.close()
                return

            vless_hdr = build_vless_request_header(
                node.uuid,
                target_addr=target_host,
                target_port=target_port,
                is_domain=is_domain,
            )
            remote_writer.write(vless_hdr)
            await remote_writer.drain()

            client_writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x2a\x38")
            await client_writer.drain()

            async def _pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter):
                try:
                    while True:
                        chunk = await r.read(16384)
                        if not chunk:
                            break
                        w.write(chunk)
                        await w.drain()
                except Exception:
                    pass
                finally:
                    with contextlib.suppress(Exception):
                        w.close()

            await asyncio.gather(
                _pipe(client_reader, remote_writer),
                _pipe(remote_reader, client_writer),
                return_exceptions=True,
            )

        except Exception as e:
            logger.debug("SOCKS5 client error: %s", e)
        finally:
            with contextlib.suppress(Exception):
                client_writer.close()

    def start(self) -> None:
        """Start the SOCKS5 server in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        ready_event = threading.Event()

        def _run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _main():
                self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
                ready_event.set()
                logger.info("Custom SOCKS5 server listening on %s:%d", self.host, self.port)
                async with self._server:
                    while not self._stop_event.is_set():
                        await asyncio.sleep(0.5)

            try:
                self._loop.run_until_complete(_main())
            except Exception as e:
                logger.debug("Proxy server stopped: %s", e)
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=_run_loop, daemon=True)
        self._thread.start()
        ready_event.wait(timeout=5.0)

    def stop(self) -> None:
        """Stop the SOCKS5 proxy server."""
        self._stop_event.set()
        if self._server and self._loop:
            self._loop.call_soon_threadsafe(self._server.close)
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None


_global_proxy_server: LocalSocks5Server | None = None
_global_proxy_lock = threading.Lock()


def get_or_start_proxy_engine(
    host: str = "127.0.0.1",
    port: int = 10808,
    custom_node: ProxyNode | None = None,
    store: ProxyStore | None = None,
) -> LocalSocks5Server:
    """Return singleton running instance of LocalSocks5Server."""
    global _global_proxy_server
    with _global_proxy_lock:
        if _global_proxy_server is None:
            pool = NodePool(store=store)
            if custom_node:
                pool.add_node(custom_node)
            _global_proxy_server = LocalSocks5Server(host=host, port=port, node_pool=pool)
            _global_proxy_server.start()
        return _global_proxy_server


def mark_active_proxy_captcha(hours: float = 24.0) -> None:
    """Flag the currently active proxy as CAPTCHA-blocked for 24h and auto-rotate."""
    global _global_proxy_server
    with _global_proxy_lock:
        if _global_proxy_server and _global_proxy_server.node_pool:
            _global_proxy_server.node_pool.rotate_node(reason="captcha")


def stop_proxy_engine() -> None:
    """Stop the global proxy engine if running."""
    global _global_proxy_server
    with _global_proxy_lock:
        if _global_proxy_server is not None:
            _global_proxy_server.stop()
            _global_proxy_server = None
