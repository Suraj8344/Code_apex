"""Headless QArena pipeline runner (dev/benchmark harness).
Runs the full Stage 2-8 pipeline against a live target, no UI needed.
Usage: python3 scripts/headless_run.py [url] [seconds_max]
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config
from core.orchestrator import Orchestrator, RunHandle

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8900"
DESC = ("CloudCart is an e-commerce store. Users sign up or log in, browse "
        "products, search the catalog, add items to a cart, apply promo code "
        "SAVE10 for 10% off, and check out with their name, email and shipping "
        "address. Cart quantities must be 1-10, emails must be valid, the "
        "shipping address is required, the price charged equals the catalog "
        "price, and the order total can never be negative.")
EMAIL = "demo@cloudcart.dev"
PWD = "demo12345"

orch = Orchestrator()
h = RunHandle(run_id=f"cli{int(time.time())}")
h.run_dir = config.ARTIFACTS / f"run_{h.run_id}"
h.run_dir.mkdir(parents=True, exist_ok=True)
from core.contracts import RunResult

h.result = RunResult(run_id=h.run_id, target_url=URL, description=DESC)
orch.runs[h.run_id] = h

def pump():
    while True:
        ev = h.events.get()
        if ev.get("type") in ("stage", "verdict", "confirmed", "flaky",
                              "suspect", "plan", "map", "log"):
            print(f"[{ev.get('ts')}] {ev}", flush=True)
        if ev.get("type") == "done":
            break

t = threading.Thread(target=pump, daemon=True)
t.start()
t0 = time.time()
orch._safe_pipeline(h, URL, DESC, EMAIL, PWD, None)
t.join(timeout=10)
print(f"\n===== finished in {time.time()-t0:.0f}s =====")
r = json.loads((h.run_dir / "results.json").read_text())
print("states:", r["states_found"], "cases:", r["tests_planned"],
      "passed:", r["tests_passed"], "steps:", r["steps_executed"])
print("filed:", len(r["filed_bugs"]), "flaky:", len(r["flaky"]))
b = r.get("benchmark") or {}
print("benchmark:", json.dumps(b, indent=2) if b else "n/a")
