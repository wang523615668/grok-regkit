#!/usr/bin/env python3
"""CLI connectivity check for proxy / email / CPA local+remote."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Optional: load CPA mgmt key for remote check
secrets = Path("/vol1/1000/openzl/cpa/.secrets.env")
if secrets.is_file():
    for line in secrets.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import grok_register_ttk as eng  # noqa: E402
from connectivity import format_check_results, run_connectivity_checks  # noqa: E402


def main() -> int:
    eng.load_config()
    results = run_connectivity_checks(eng.config, eng.http_get, eng.http_post)
    print(format_check_results(results))
    return 0 if all(ok for _n, ok, _d in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
