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
        for i, ce in enumerate(ev.console_errors):
            if i not in attributed:
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

        # ---- S6 groups from deterministic intent findings -------------------
        for fnd in ev.intent_findings:
            add(f"intent:{fnd[:48]}", _hit(6, fnd))

        # ---- S5 groups per serious axe violation id -------------------------
        # Structural naming violations may be filed on their own strength;
        # other serious axe hits stay assistive (need a second signal).
        SOLO_OK = {"label", "button-name", "input-image-alt", "image-alt",
                   "select-name", "frame-title", "aria-required-children",
                   "color-contrast", "a11y-placeholder-only-label"}
        for v in ev.axe_violations:
            if v.get("impact") in ("serious", "critical"):
                hit = _hit(5, f"{v['id']} on {v.get('target', 'page')} — "
                              f"{v.get('help', '')}")
                if v["id"] in SOLO_OK:
                    hit.weight = 0.55
                add(f"axe:{v['id']}", hit)

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
