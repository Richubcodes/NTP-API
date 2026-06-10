"""FastAPI app exposing the NTP fleet status and a simple HTML dashboard.

Endpoints:
    GET  /api/status       -> latest poll per host (JSON)
    GET  /api/summary      -> aggregate counts (JSON)
    GET  /api/host/{host}  -> history for one host (JSON)
    POST /api/poll         -> trigger an immediate poll cycle
    GET  /                 -> HTML dashboard
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.poller import poll_fleet
from app.store import PollStore

logger = logging.getLogger(__name__)

HOSTS_FILE = Path(os.environ.get("NTP_HOSTS_FILE", "config/hosts.txt"))
DB_PATH = os.environ.get("NTP_DB_PATH", "ntp_monitor.db")

app = FastAPI(
    title="NTP Fleet Monitor",
    description="Read-only visibility into NTP server health across a fleet.",
    version="1.0.0",
)

store = PollStore(DB_PATH)


def _load_hosts() -> list[str]:
    if not HOSTS_FILE.exists():
        logger.warning("Hosts file %s not found; returning empty list", HOSTS_FILE)
        return []
    return [
        line.strip()
        for line in HOSTS_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


@app.on_event("startup")
async def initial_poll() -> None:
    """Run a first poll on startup so the dashboard isn't empty."""
    hosts = _load_hosts()
    if hosts:
        results = await poll_fleet(hosts)
        store.record_results(results)
        logger.info("Initial poll completed for %d hosts", len(hosts))


@app.get("/api/status")
def get_status() -> dict:
    return {"hosts": store.latest_per_host()}


@app.get("/api/summary")
def get_summary() -> dict:
    return store.summary()


@app.get("/api/host/{host}")
def get_host_history(host: str, limit: int = 50) -> dict:
    history = store.history_for_host(host, limit=limit)
    if not history:
        raise HTTPException(status_code=404, detail=f"No data for host {host}")
    return {"host": host, "history": history}


@app.post("/api/poll")
async def trigger_poll() -> dict:
    hosts = _load_hosts()
    if not hosts:
        raise HTTPException(status_code=400, detail="No hosts configured")
    results = await poll_fleet(hosts)
    store.record_results(results)
    return {"polled": len(results), "summary": store.summary()}


# Background scheduler: re-poll every POLL_INTERVAL_SECONDS.
@app.on_event("startup")
async def start_scheduler() -> None:
    interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                hosts = _load_hosts()
                if hosts:
                    results = await poll_fleet(hosts)
                    store.record_results(results)
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled poll failed")

    asyncio.create_task(_loop())


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>NTP Fleet Monitor</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; background: #f5f6fa; color: #1f2937; }
  header { background: #1f4e79; color: white; padding: 20px 32px; }
  header h1 { margin: 0; font-size: 22px; font-weight: 600; }
  header p  { margin: 4px 0 0; opacity: 0.85; font-size: 13px; }
  main { padding: 24px 32px; max-width: 1100px; margin: 0 auto; }
  .summary { display: grid; grid-template-columns: repeat(4, 1fr);
             gap: 12px; margin-bottom: 24px; }
  .card { background: white; border-radius: 8px; padding: 16px;
          box-shadow: 0 1px 3px rgba(0,0,0,.06); }
  .card .n { font-size: 28px; font-weight: 700; }
  .card .label { font-size: 12px; text-transform: uppercase;
                 letter-spacing: .04em; color: #6b7280; }
  table { width: 100%; border-collapse: collapse; background: white;
          border-radius: 8px; overflow: hidden;
          box-shadow: 0 1px 3px rgba(0,0,0,.06); }
  th, td { padding: 12px 16px; text-align: left; font-size: 14px;
           border-bottom: 1px solid #e5e7eb; }
  th { background: #f9fafb; font-weight: 600; color: #374151;
       text-transform: uppercase; font-size: 11px; letter-spacing: .04em; }
  .pill { display: inline-block; padding: 3px 10px; border-radius: 999px;
          font-size: 12px; font-weight: 600; }
  .healthy { background: #dcfce7; color: #166534; }
  .drifting { background: #fef9c3; color: #854d0e; }
  .unsynchronised { background: #fed7aa; color: #9a3412; }
  .unreachable { background: #fee2e2; color: #991b1b; }
  button { background: #1f4e79; color: white; border: 0; padding: 8px 16px;
           border-radius: 6px; cursor: pointer; font-size: 14px; }
  button:hover { background: #163d5e; }
  .muted { color: #6b7280; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>NTP Fleet Monitor</h1>
  <p>Read-only view of NTP server health across the fleet</p>
</header>
<main>
  <div class="summary" id="summary"></div>
  <p><button onclick="refresh(true)">Trigger poll now</button>
     <span class="muted" id="lastUpdated"></span></p>
  <table>
    <thead>
      <tr>
        <th>Host</th><th>Status</th><th>Stratum</th>
        <th>Offset (ms)</th><th>Delay (ms)</th><th>Last polled</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
</main>
<script>
async function refresh(triggerPoll = false) {
  if (triggerPoll) await fetch('/api/poll', { method: 'POST' });
  const [statusRes, summaryRes] = await Promise.all([
    fetch('/api/status').then(r => r.json()),
    fetch('/api/summary').then(r => r.json()),
  ]);
  renderSummary(summaryRes);
  renderRows(statusRes.hosts);
  document.getElementById('lastUpdated').textContent =
    'Updated ' + new Date().toLocaleTimeString();
}
function renderSummary(s) {
  const cards = [
    { label: 'Total hosts', n: s.total, cls: '' },
    { label: 'Healthy', n: s.by_status.healthy || 0, cls: 'healthy' },
    { label: 'Drifting', n: s.by_status.drifting || 0, cls: 'drifting' },
    { label: 'Unreachable', n: (s.by_status.unreachable || 0)
        + (s.by_status.unsynchronised || 0), cls: 'unreachable' },
  ];
  document.getElementById('summary').innerHTML = cards.map(c => `
    <div class="card">
      <div class="n">${c.n}</div>
      <div class="label">${c.label}</div>
    </div>`).join('');
}
function renderRows(hosts) {
  document.getElementById('rows').innerHTML = hosts.map(h => `
    <tr>
      <td><strong>${h.host}</strong></td>
      <td><span class="pill ${h.status}">${h.status}</span></td>
      <td>${h.stratum ?? '-'}</td>
      <td>${h.offset_ms != null ? h.offset_ms.toFixed(2) : '-'}</td>
      <td>${h.delay_ms != null ? h.delay_ms.toFixed(2) : '-'}</td>
      <td class="muted">${h.queried_at_utc}</td>
    </tr>`).join('');
}
refresh();
setInterval(() => refresh(false), 30000);
</script>
</body>
</html>"""
