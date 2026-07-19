#!/usr/bin/env python3
"""Batch hybrid register: python run_hybrid_n.py [count]"""
import os
import sys
import traceback

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import grok_register_ttk as eng
from hybrid_register import run_hybrid_registration_job


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    eng.load_config()
    print("[*] mode=", eng.config.get("register_mode"))
    print("[*] target_count=", count)
    print("[*] email=", eng.config.get("email_provider"), eng.config.get("cloudflare_api_base"))
    print("[*] proxy=", eng.config.get("proxy"))
    print(
        "[*] cpa_export=",
        eng.config.get("cpa_export_enabled"),
        "prefer_protocol=",
        eng.config.get("cpa_prefer_protocol"),
        "hotload=",
        eng.config.get("cpa_hotload_dir"),
    )
    try:
        res = run_hybrid_registration_job(count, log_callback=print)
        print("[RESULT]", res)
        if not res or int(res.get("success") or 0) <= 0:
            raise SystemExit(2)
    except SystemExit:
        raise
    except Exception as exc:
        print("[FATAL]", exc)
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
