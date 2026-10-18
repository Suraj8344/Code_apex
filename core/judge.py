"""
Stage 5b — The 6-Signal Bug Judge. Signals are attributed to EVIDENCE GROUPS
(one group == one candidate defect) instead of one blob per step:
  - each failing HTTP request -> its own group (console/errors + broken items
    that reference the same resource are attributed into it)
  - unattributed console errors / broken items / contract failures /
    intent findings / axe violations -> one group each kind
Decision rule per group (architecture): score >= 0.50 OR 2+ signals agree.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from . import config
from .contracts import PossibleBug, SignalHit, StepEvidence

W = config.SIGNAL_WEIGHTS


def _hit(sig: int, evidence: str) -> SignalHit:
    return SignalHit(signal=sig, name=W[sig][0], weight=W[sig][1],
                     evidence=re.sub(r"\s+", " ", evidence)[:220])


class Judge:
    def __init__(self, llm=None) -> None:
        self.llm = llm  # reserved for Signal 6 upgrade (LLM #2)

    def evaluate(self, journey: str, test_id: str, step_index: int,
                 ev: StepEvidence, repro_steps) -> list[PossibleBug]:
        groups: dict[str, dict] = {}

        def add(group_key: str, hit: SignalHit) -> None:
            g = groups.setdefault(group_key, {"key": group_key, "hits": [],
                                              "signals": set()})
            if hit.signal not in g["signals"]:
                g["signals"].add(hit.signal)
                g["hits"].append(hit)

        # ---- S2 groups per failing URL (+ attribute S1/S3 sharing the URL) --
        for f in ev.http_failures:
            # Static asset blocks (often CDN/WAF/browser policy responses) are
            # useful trace context, but do not prove a broken user journey.
            if f.get("resource_type") not in (None, "document", "xhr", "fetch"):
                continue
            path = urlparse(f["url"]).path or f["url"]
            key = f"http:{path}"
            add(key, _hit(2, f"{f['method']} {f['url']} → {f['status']}"))
            token = path.split("/")[-1]
            for ce in ev.console_errors:
                if token and token in ce:
                    add(key, _hit(1, ce))
            for b in ev.broken_items:
                if token and (token in (b.get("detail") or "")
                              or token in (b.get("name") or "")):
                    add(key, _hit(3, f"{b['kind']}: {b.get('name') or b.get('detail')}"))

        # ---- S1 groups for remaining console errors -------------------------
        attributed = {i for i, c in enumerate(ev.console_errors)
                      for f in ev.http_failures
                      if urlparse(f["url"]).path.split("/")[-1] in c}
        failed_script_assets = any(
            f.get("resource_type") == "script" for f in ev.http_failures)
        for i, ce in enumerate(ev.console_errors):
            if i not in attributed:
                if failed_script_assets and "unexpected token '<'" in ce.lower():
                    continue
                hit = _hit(1, ce)
                if ce.startswith("[uncaught]"):
                    # pageerror: a real runtime exception, not load noise —
                    # catastrophic enough to be filed on its own
                    hit.weight = 0.60
                add(f"console:{ce[:48]}", hit)

        # ---- S3 groups for remaining broken items ---------------------------
        attributed_b = {j for j, b in enumerate(ev.broken_items)
                        for f in ev.http_failures
                        if urlparse(f["url"]).path.split("/")[-1]
                        in ((b.get("detail") or "") + (b.get("name") or ""))}
        for j, b in enumerate(ev.broken_items):
            if j not in attributed_b:
                add(f"broken:{(b.get('name') or b.get('detail'))[:48]}",
                    _hit(3, f"{b['kind']}: {b.get('name') or b.get('detail')}"))

        # ---- S4 / S6 groups from contract failures --------------------------
        for c in ev.contract_failures:
            if c.startswith("form:"):
                add(f"form:{c[5:][:48]}", _hit(4, c[5:]))
            elif c.startswith("flow:"):
                add(f"flow:{c[5:][:48]}", _hit(6, c[5:]))
            elif c.startswith("expect:"):
                add(f"expect:{c[7:][:48]}", _hit(6, c[7:]))
            elif c.startswith("control:"):
                add(f"control:{c[8:][:48]}", _hit(6, c[8:]))

        # ---- S6 groups from deterministic intent findings -------------------
        for fnd in ev.intent_findings:
            add(f"intent:{fnd[:48]}", _hit(6, fnd))

        # Include every axe impact level. Axe supplies rule-specific evidence;
        # replay verification filters transient page states before filing.
        for v in ev.axe_violations:
            impact = v.get("impact", "minor")
            hit = _hit(5, f"{v['id']} ({impact}) on {v.get('target', 'page')} — "
                          f"{v.get('failure_summary') or v.get('help', '')}")
            # A single deterministic accessibility rule hit is a reportable
            # minor finding after the normal replay gate, even without a second signal.
            if impact == "minor":
                hit.weight = 0.51
            elif impact == "moderate":
                hit.weight = 0.55
            elif impact in ("serious", "critical"):
                hit.weight = 0.60
            add(f"axe:{v['id']}:{v.get('target', '')}", hit)

        out: list[PossibleBug] = []
        for g in groups.values():
            hits = g["hits"]
            score = min(1.0, round(sum(h.weight for h in hits), 3))
            if score < config.BUG_SCORE_THRESHOLD and len(hits) < config.BUG_MIN_AGREEING:
                continue
            out.append(PossibleBug(
                journey=journey, test_id=test_id, step_index=step_index,
                group=g["key"], signals=hits, score=score,
                summary=" | ".join(f"S{h.signal} {h.name}: {h.evidence[:90]}"
                                   for h in hits),
                repro_steps=list(repro_steps), evidence=ev))
        return out
