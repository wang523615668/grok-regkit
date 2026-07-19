#!/usr/bin/env python3
"""One-shot hybrid register for local smoke test."""
import os
import traceback

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import grok_register_ttk as eng
from hybrid_register import run_hybrid_registration_job


def main():
    eng.load_config()
    print("[*] mode=", eng.config.get("register_mode"))
    print("[*] count=", eng.config.get("register_count"))
    print("[*] email=", eng.config.get("email_provider"), eng.config.get("cloudflare_api_base"))
    print("[*] proxy=", eng.config.get("proxy"))
    print("[*] cpa_export=", eng.config.get("cpa_export_enabled"), "prefer_protocol=", eng.config.get("cpa_prefer_protocol"))
    try:
        res = run_hybrid_registration_job(1, log_callback=print)
        print("[RESULT]", res)
    except Exception as exc:
        print("[FATAL]", exc)
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
