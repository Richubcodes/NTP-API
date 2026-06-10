"""CLI for ad-hoc polling. Useful for cron jobs and quick health checks.

Examples:
    python -m app.cli poll pool.ntp.org time.cloudflare.com
    python -m app.cli poll --file config/hosts.txt --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.poller import poll_fleet


def _load_hosts_from_file(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _format_human(results: list) -> str:
    rows = ["HOST                          STATUS         STRATUM  OFFSET(ms)"]
    rows.append("-" * 70)
    for r in results:
        offset = f"{r.offset_ms:.2f}" if r.offset_ms is not None else "-"
        stratum = str(r.stratum) if r.stratum is not None else "-"
        rows.append(f"{r.host:<30} {r.status:<14} {stratum:<8} {offset}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NTP fleet poller CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    poll = sub.add_parser("poll", help="Poll one or more NTP servers")
    poll.add_argument("hosts", nargs="*", help="Hostnames to query")
    poll.add_argument("--file", type=Path, help="File with one host per line")
    poll.add_argument("--json", action="store_true", help="Emit JSON output")

    args = parser.parse_args(argv)

    if args.cmd == "poll":
        hosts = list(args.hosts)
        if args.file:
            hosts.extend(_load_hosts_from_file(args.file))
        if not hosts:
            print("No hosts supplied", file=sys.stderr)
            return 2
        results = asyncio.run(poll_fleet(hosts))
        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2))
        else:
            print(_format_human(results))
        # Non-zero exit if anything is unhealthy - handy for cron.
        unhealthy = [r for r in results if r.status != "healthy"]
        return 1 if unhealthy else 0

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
