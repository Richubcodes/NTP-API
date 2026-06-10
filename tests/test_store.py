"""Tests for the SQLite-backed PollStore."""

from __future__ import annotations

from datetime import datetime, timezone

from app.poller import NTPResult
from app.store import PollStore


def _result(host: str, status: str = "healthy", **kwargs) -> NTPResult:
    defaults = dict(
        reachable=True,
        stratum=2,
        offset_ms=2.5,
        delay_ms=10.0,
        server_time_utc=datetime.now(timezone.utc).isoformat(),
        queried_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    if status == "unreachable":
        defaults.update(reachable=False, stratum=None, offset_ms=None, delay_ms=None, error="timeout")
    defaults.update(kwargs)
    return NTPResult(host=host, **defaults)


def test_record_and_fetch_latest(tmp_path):
    store = PollStore(tmp_path / "test.db")
    store.record_results([_result("a"), _result("b"), _result("c")])
    latest = store.latest_per_host()
    assert {r["host"] for r in latest} == {"a", "b", "c"}


def test_latest_returns_most_recent(tmp_path):
    store = PollStore(tmp_path / "test.db")
    store.record_results([_result("a", offset_ms=1.0, queried_at_utc="2024-01-01T00:00:00")])
    store.record_results([_result("a", offset_ms=999.0, queried_at_utc="2024-01-01T01:00:00")])
    latest = store.latest_per_host()
    assert len(latest) == 1
    assert latest[0]["offset_ms"] == 999.0


def test_summary_counts(tmp_path):
    store = PollStore(tmp_path / "test.db")
    store.record_results([
        _result("a", status="healthy"),
        _result("b", status="healthy"),
        _result("c", status="unreachable"),
    ])
    summary = store.summary()
    assert summary["total"] == 3
    assert summary["by_status"]["healthy"] == 2
    assert summary["by_status"]["unreachable"] == 1


def test_history_for_host_limits(tmp_path):
    store = PollStore(tmp_path / "test.db")
    for i in range(5):
        store.record_results([_result("a", queried_at_utc=f"2024-01-01T0{i}:00:00")])
    history = store.history_for_host("a", limit=3)
    assert len(history) == 3
    assert history[0]["queried_at_utc"] > history[-1]["queried_at_utc"]
