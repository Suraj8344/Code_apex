"""
Stage 2 [Python Config] — Run limits, 6-signal weights, decision thresholds.
Everything tunable in one place. Architecture section 2/5b values.
"""
from pathlib import Path
import os as _os

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = Path(_os.environ.get(
    "QA_ARTIFACTS_DIR",
    "/tmp/qarena-artifacts" if _os.environ.get("VERCEL") else ROOT / "artifacts",
))
AUTH_STATE = ROOT / "auth_state.json"
AXE_JS = ROOT / "assets" / "axe.min.js"
JINJA_DIR = ROOT / "jinja_templates"
# Ground-truth for the benchmark scorer (Stage 8): point QA_TRUTH_JSON at any
# planted-bug catalog ({url, bugs:[{id,title,signals,hints}]}) for the target
# under test. No file => benchmark stage reports gracefully as unavailable.
SAMPLE_APP_BUGS = Path(_os.environ.get("QA_TRUTH_JSON",
                                       ROOT / "sample_app" / "bugs.json"))

# ---- Hard run limits (Stage 2 step 4) -----------------------------------
MAX_STATES = 22              # explorer: unique page states
MAX_CRAWL_ACTIONS = 60       # explorer: crawl actions
MAX_STEPS_PER_TEST = 14      # tester: steps per generated test case
MAX_TESTS = 16               # planner output cap executed
RUN_WALL_CLOCK_S = 480       # whole-run budget
STEP_TIMEOUT_MS = 3500       # element resolution timeout
NAV_TIMEOUT_MS = 10000

# ---- 6-Signal Bug Judge (Stage 5b, weights per architecture table) ------
SIGNAL_WEIGHTS = {
    1: ("Console Errors", 0.45),
    2: ("Network Failures", 0.50),
    3: ("Broken Page Items", 0.30),
    4: ("Form Checks", 0.50),
    5: ("Accessibility", 0.25),
    6: ("Intent Check", 0.60),
}
BUG_SCORE_THRESHOLD = 0.50   # flag if combined score >= threshold …
BUG_MIN_AGREEING = 2         # … OR if 2+ signals agree

# ---- Stage 6a pass^3 replay gate ----------------------------------------
REPLAY_TIMES = 3             # clean-browser repetitions
REPLAY_CONFIRM_MIN = 2       # reproduced >= 2/3 => CONFIRMED, else FLAKY

# ---- Exploration safety (Stage 2 safety guard + explorer policy) --------
# The explorer only follows GET navigation (links + search). Buttons and
# state-mutating links are recorded as elements, never clicked, during crawl.
# Route-guard profiles. EXPLORER must never POST anything mutating while it
# maps the territory; TESTER deliberately provokes POSTs (that IS the test),
# so it only blocks true session/fixture destroyers.
NAV_BLOCKED_EXPLORER = ("logout", "delete", "remove", "/admin", "/__seed__",
                        "checkout/pay", "cart/update", "cart/promo")
NAV_BLOCKED_TESTER = ("logout", "/admin", "/__seed__", "/api/delete")
# back-compat alias
NAV_BLOCKED_PATTERNS = NAV_BLOCKED_EXPLORER
SEARCH_PROBE = "neon"        # safe GET probe used to discover the search route

# ---- LLM (LiteLLM; whole pipeline also runs key-free via heuristics) ----
LLM_MODEL_ENV_DEFAULT = "gemini/gemini-2.0-flash"
LLM_MAX_TOKENS = 2200
LLM_TIMEOUT_S = 45

UI_PORT = 8088

HEADLESS = True
VIEWPORT = {"width": 1360, "height": 900}
