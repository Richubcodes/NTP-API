# NTP Fleet Monitor

Read-only visibility into NTP server health across a fleet. Designed to eliminate the need for engineers to hold elevated permissions just to check whether time synchronisation is working.

[![CI](https://github.com/YOUR_USERNAME/ntp-fleet-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USERNAME/ntp-fleet-monitor/actions)

## Why this exists

In large network environments, NTP infrastructure is critical but invisible. Engineers historically need elevated access to query NTP servers directly - every health check becomes a security risk and a functionality risk when conducted by someone new to the field.

This service polls NTP servers using the NTPv3 protocol, stores results, and exposes a dashboard and REST API so anyone on the team can see the fleet's state without requiring privileged access.

## Features

- **Real-time polling** of any NTP server using raw NTPv3 protocol (no external NTP libraries - just `socket` and `struct`)
- **Status classification**: healthy / drifting / unsynchronised / unreachable based on stratum and offset
- **REST API** for integration with monitoring stacks
- **HTML dashboard** with auto-refresh, zero build step
- **CLI mode** for cron jobs and ad-hoc checks
- **SQLite history** - last N polls per host kept indefinitely
- **Background scheduler** - re-polls every 60s by default
- **Dockerised** with persistent volume for poll history
- **CI** on every PR (lint + tests + Docker build smoke test)

## Quick start

### With Docker (recommended)

```bash
git clone https://github.com/YOUR_USERNAME/ntp-fleet-monitor.git
cd ntp-fleet-monitor
docker compose up -d
open http://localhost:8000
```

### Local Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### CLI

```bash
# Poll a few servers and print a table
python -m app.cli poll pool.ntp.org time.cloudflare.com

# Poll from a file, output JSON, exit non-zero if anything's unhealthy
python -m app.cli poll --file config/hosts.txt --json
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NTP_HOSTS_FILE` | `config/hosts.txt` | One hostname per line |
| `NTP_DB_PATH` | `ntp_monitor.db` | SQLite file path |
| `POLL_INTERVAL_SECONDS` | `60` | Background poll interval |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Latest poll for every host |
| GET | `/api/summary` | Aggregate counts by status |
| GET | `/api/host/{host}` | Recent poll history for one host |
| POST | `/api/poll` | Trigger an immediate poll cycle |

Example:
```bash
curl http://localhost:8000/api/summary
# {"total": 5, "by_status": {"healthy": 4, "drifting": 1, ...}}
```

## How it works

1. `app.poller` builds a raw 48-byte NTPv3 client packet (`LI=0, VN=3, Mode=3`), sends it over UDP, parses the response, and computes offset/delay using the classic NTP timestamp algorithm: `offset = ((t2 - t1) + (t3 - t4)) / 2`.
2. `app.store` writes results to SQLite with a `(host, queried_at_utc DESC)` index for fast latest-per-host queries.
3. `app.main` is a FastAPI app that wraps the poller and store, runs a background scheduler, and serves the dashboard.

## Testing

```bash
pip install -r requirements-dev.txt
pytest -v
```

Tests mock the network layer so the suite runs offline in CI.

## Roadmap

- [ ] Prometheus `/metrics` endpoint for Grafana dashboards
- [ ] Per-host alert thresholds (e.g. offset > 50ms for 5 mins)
- [ ] Webhook notifications (Slack, PagerDuty)
- [ ] Optional Postgres backend for multi-instance deployments

## Licence

MIT
