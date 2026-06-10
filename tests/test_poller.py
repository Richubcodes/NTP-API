"""Tests for the NTP poller.

Network calls are mocked so tests run in isolation. We exercise the
packet build/parse cycle and the error paths.
"""

from __future__ import annotations

import socket
import struct
import time
from unittest.mock import MagicMock, patch

import pytest

from app.poller import (
    NTP_PACKET_SIZE,
    NTP_UNIX_EPOCH_DELTA,
    NTPResult,
    _build_ntp_packet,
    _parse_ntp_response,
    query_ntp_server,
)


def test_build_ntp_packet_is_48_bytes():
    pkt = _build_ntp_packet()
    assert len(pkt) == NTP_PACKET_SIZE
    # First byte should be 0x1B: LI=0, VN=3, Mode=3
    assert pkt[0] == 0x1B


def _make_response(stratum: int, t2: float, t3: float) -> bytes:
    """Construct a synthetic NTP response packet."""
    fields = [0] * 12
    header = (0 << 30) | (3 << 27) | (4 << 24) | (stratum << 16)
    fields[0] = header
    t2_secs = int(t2 + NTP_UNIX_EPOCH_DELTA)
    t2_frac = int((t2 + NTP_UNIX_EPOCH_DELTA - t2_secs) * 2**32) & 0xFFFFFFFF
    t3_secs = int(t3 + NTP_UNIX_EPOCH_DELTA)
    t3_frac = int((t3 + NTP_UNIX_EPOCH_DELTA - t3_secs) * 2**32) & 0xFFFFFFFF
    fields[8] = t2_secs
    fields[9] = t2_frac
    fields[10] = t3_secs
    fields[11] = t3_frac
    return struct.pack("!12I", *fields)


def test_parse_ntp_response_basic():
    t1 = 1_700_000_000.0
    t4 = t1 + 0.01
    t2 = t1 + 0.005
    t3 = t2 + 0.001
    data = _make_response(stratum=2, t2=t2, t3=t3)
    stratum, offset, delay, server_time = _parse_ntp_response(data, t1, t4)
    assert stratum == 2
    assert abs(offset) < 0.01
    assert delay > 0
    assert abs(server_time - t3) < 0.001


def test_parse_ntp_response_rejects_short_packet():
    with pytest.raises(ValueError):
        _parse_ntp_response(b"\x00" * 10, 0.0, 1.0)


@patch("app.poller.socket.socket")
def test_query_ntp_server_timeout(mock_socket_cls):
    mock_sock = MagicMock()
    mock_sock.recvfrom.side_effect = socket.timeout
    mock_socket_cls.return_value = mock_sock

    result = query_ntp_server("fake.example.com")
    assert isinstance(result, NTPResult)
    assert result.reachable is False
    assert result.error == "timeout"
    assert result.status == "unreachable"


@patch("app.poller.socket.socket")
def test_query_ntp_server_dns_failure(mock_socket_cls):
    mock_sock = MagicMock()
    mock_sock.sendto.side_effect = socket.gaierror("name not known")
    mock_socket_cls.return_value = mock_sock

    result = query_ntp_server("nope.invalid")
    assert result.reachable is False
    assert "dns_failure" in (result.error or "")


@patch("app.poller.socket.socket")
def test_query_ntp_server_success(mock_socket_cls):
    mock_sock = MagicMock()
    t_now = time.time()
    response = _make_response(stratum=2, t2=t_now + 0.001, t3=t_now + 0.002)
    mock_sock.recvfrom.return_value = (response, ("1.2.3.4", 123))
    mock_socket_cls.return_value = mock_sock

    result = query_ntp_server("time.example.com")
    assert result.reachable is True
    assert result.stratum == 2
    assert result.status == "healthy"
    assert result.offset_ms is not None


def test_ntp_result_status_classifications():
    assert NTPResult(host="x", reachable=False).status == "unreachable"
    assert NTPResult(host="x", reachable=True, stratum=16).status == "unsynchronised"
    assert NTPResult(host="x", reachable=True, stratum=2, offset_ms=250).status == "drifting"
    assert NTPResult(host="x", reachable=True, stratum=2, offset_ms=5).status == "healthy"
