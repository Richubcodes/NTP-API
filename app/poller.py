"""NTP server polling using NTPv3 client queries.

Queries a fleet of NTP servers and returns sync status, stratum, offset,
and reachability. Designed to be called from the API layer or run
standalone as a CLI for ad-hoc checks.

The poller is deliberately stateless: callers store results in their
own database or in-memory cache. This keeps the module easy to test
and easy to drop into a cron job, a scheduler, or a web request.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Iterable

logger = logging.getLogger(__name__)

# NTP epoch is 1900-01-01; Unix epoch is 1970-01-01.
# Difference in seconds.
NTP_UNIX_EPOCH_DELTA = 2_208_988_800
NTP_PACKET_FORMAT = "!12I"
NTP_PACKET_SIZE = 48
DEFAULT_NTP_PORT = 123
DEFAULT_TIMEOUT_SECONDS = 3.0


@dataclass
class NTPResult:
    """Outcome of a single NTP query."""

    host: str
    reachable: bool
    stratum: int | None = None
    offset_ms: float | None = None
    delay_ms: float | None = None
    server_time_utc: str | None = None
    error: str | None = None
    queried_at_utc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def status(self) -> str:
        """High-level health label used by the dashboard."""
        if not self.reachable:
            return "unreachable"
        if self.stratum is None or self.stratum >= 16:
            return "unsynchronised"
        if self.offset_ms is not None and abs(self.offset_ms) > 100:
            return "drifting"
        return "healthy"


def _build_ntp_packet() -> bytes:
    """Build a minimal NTPv3 client request packet (LI=0, VN=3, Mode=3)."""
    # LI (2 bits) | VN (3 bits) | Mode (3 bits) packed into first byte.
    # 0b00 | 0b011 | 0b011 = 0x1B
    first_byte = 0x1B
    packet = bytearray(NTP_PACKET_SIZE)
    packet[0] = first_byte
    return bytes(packet)


def _parse_ntp_response(data: bytes, t1: float, t4: float) -> tuple[int, float, float, float]:
    """Return (stratum, offset_seconds, delay_seconds, server_time_unix)."""
    if len(data) < NTP_PACKET_SIZE:
        raise ValueError(f"NTP response too short: {len(data)} bytes")

    unpacked = struct.unpack(NTP_PACKET_FORMAT, data[:NTP_PACKET_SIZE])
    stratum = (unpacked[0] >> 16) & 0xFF

    # Receive timestamp (server) - fields 8 & 9 (seconds + fraction).
    t2_secs, t2_frac = unpacked[8], unpacked[9]
    # Transmit timestamp (server) - fields 10 & 11.
    t3_secs, t3_frac = unpacked[10], unpacked[11]

    t2 = (t2_secs - NTP_UNIX_EPOCH_DELTA) + (t2_frac / 2**32)
    t3 = (t3_secs - NTP_UNIX_EPOCH_DELTA) + (t3_frac / 2**32)

    # Classic NTP offset/delay calculation.
    offset = ((t2 - t1) + (t3 - t4)) / 2
    delay = (t4 - t1) - (t3 - t2)

    return stratum, offset, delay, t3


def query_ntp_server(
    host: str,
    port: int = DEFAULT_NTP_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> NTPResult:
    """Synchronously query a single NTP server.

    Returns an NTPResult; never raises. Network and parse errors are
    captured in the result's `error` field.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    try:
        packet = _build_ntp_packet()
        t1 = time.time()
        sock.sendto(packet, (host, port))
        data, _ = sock.recvfrom(NTP_PACKET_SIZE * 2)
        t4 = time.time()

        stratum, offset_s, delay_s, server_time = _parse_ntp_response(data, t1, t4)
        return NTPResult(
            host=host,
            reachable=True,
            stratum=stratum,
            offset_ms=offset_s * 1000,
            delay_ms=delay_s * 1000,
            server_time_utc=datetime.fromtimestamp(server_time, tz=timezone.utc).isoformat(),
            queried_at_utc=now_iso,
        )
    except socket.timeout:
        return NTPResult(host=host, reachable=False, error="timeout", queried_at_utc=now_iso)
    except socket.gaierror as exc:
        return NTPResult(host=host, reachable=False, error=f"dns_failure: {exc}", queried_at_utc=now_iso)
    except Exception as exc:  # noqa: BLE001 - we want to surface any error
        logger.exception("NTP query failed for %s", host)
        return NTPResult(host=host, reachable=False, error=str(exc), queried_at_utc=now_iso)
    finally:
        sock.close()


async def query_ntp_server_async(
    host: str,
    port: int = DEFAULT_NTP_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> NTPResult:
    """Async wrapper around the sync query. Uses a thread to avoid blocking."""
    return await asyncio.to_thread(query_ntp_server, host, port, timeout)


async def poll_fleet(
    hosts: Iterable[str],
    port: int = DEFAULT_NTP_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[NTPResult]:
    """Poll many NTP servers concurrently."""
    tasks = [query_ntp_server_async(h, port, timeout) for h in hosts]
    return await asyncio.gather(*tasks)
