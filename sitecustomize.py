"""Auto-loaded by Python at startup (it's on sys.path when scripts run from the
project root). When COSTLOG is set, it turns on the API cost/request logger for
this process — so every pipeline subprocess launched by run.py logs its paid
calls without any script needing to import anything.

Enable a whole run with:
    COSTLOG=1 python run.py "<link>"
"""
import os

if os.environ.get("COSTLOG"):
    try:
        import costlog
        costlog.enable()
    except Exception as e:  # never let logging break the actual run
        import sys
        print(f"[COST] could not enable logger: {e}", file=sys.stderr)
