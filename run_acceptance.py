from __future__ import annotations

import argparse
import json
from pathlib import Path

from vpsdash.service import VpsDashService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run VPSdash acceptance checks for a configured host.")
    parser.add_argument("host_id", type=int, help="Control-plane host id")
    parser.add_argument("--capture-inventory", action="store_true", help="Refresh host inventory before the acceptance report")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    service = VpsDashService(root)
    if args.capture_inventory:
        service.capture_platform_host_inventory(args.host_id, actor="acceptance-cli")
    report = service.host_acceptance_report(args.host_id)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

