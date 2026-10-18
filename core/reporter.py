"""
Stage 7a — Reporter Agent: dedup + severity + report generation.
[Python Signature Match] merge duplicate bugs
[Python Rules] deterministic severity      [LLM #4] title/summary prose ONLY
[Jinja2 Template] fill report from saved factual data — never from model memory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import config
from .contracts import FiledBug, PossibleBug
from .llm import LLMPolicy

SEVERITY_RANK = {"Critical": 3, "Major": 2, "Minor": 1}


class Reporter:
    def __init__(self, run_dir: Path, llm: LLMPolicy) -> None:
        self.run_dir = run_dir
        self.llm = llm
        self.jinja = Environment(
            loader=FileSystemLoader(str(config.JINJA_DIR)), autoescape=False)

    # ------------------------------------------------------- dedup + file
    def file_bugs(self, confirmed: list[tuple[PossibleBug, str]]) -> list[FiledBug]:
        merged: dict[tuple, dict] = {}
        for pb, repro in confirmed:
            ev = pb.evidence
            route = self._route_of(ev.url_after or ev.url_before)
            elem = self._element_of(pb)
            # merge key includes test_id: co-located signals from the SAME
            # journey are one defect (promo 500 + "required text missing"),
            # while distinct journeys landing on the same route+element are
            # distinct defects (empty-cart pay vs missing-address pay)
            key = (route, re.sub(r"\s+", " ", elem.lower()).strip(), pb.test_id)
            sev = self._severity(pb)
            if key not in merged:
                merged[key] = {"pb": pb, "repro": repro, "route": route,
                               "elem": elem, "sev": sev, "score": pb.score,
                               "signals": [h for h in pb.signals]}
            else:
                m = merged[key]
                have = {h.signal for h in m["signals"]}
                for h in pb.signals:
                    if h.signal not in have:
                        m["signals"].append(h)
                if repro.split("/")[0] > m["repro"].split("/")[0]:
                    m["repro"] = repro
                if SEVERITY_RANK[sev] > SEVERITY_RANK[m["sev"]]:
                    m["sev"] = sev
                if pb.score > m["score"]:
                    m["score"], m["pb"] = pb.score, pb

        filed: list[FiledBug] = []
        reports_dir = self.run_dir / "bug_reports"
        reports_dir.mkdir(exist_ok=True)
        # cross-step co-assembly INSIDE one journey: groups fired by the same
        # step / route are one defect event (BUG-01: silently-accepted qty +
        # negative money + price intent all born of the same Update click).
        coalesced: dict[tuple, dict] = {}
        for m in merged.values():
            pb = m["pb"]
            # route-insensitive groups already deduped globally — pin them to
            # the richest step's filing instead (keeps console/intent/etc at one)
            if pb.group.startswith(("intent:", "console:", "form:money",
                                    "flow:", "axe:")):
                ckey = ("content", pb.test_id, "money" if "money" in pb.group or
                        "money" in pb.summary.lower() or "-$" in pb.summary
                        else pb.group.split(":")[0])
            else:
                ckey = ("http", self._route_of(
                    pb.evidence.url_after or pb.evidence.url_before), pb.test_id)
            tgt = coalesced.setdefault(ckey, m)
            if tgt is not m:
                have = {h.signal for h in tgt["signals"]}
                for h in pb.signals:
                    if h.signal not in have:
                        tgt["signals"].append(h)
                        have.add(h.signal)
                if pb.score > tgt["score"]:
                    tgt["score"], tgt["pb"] = pb.score, pb
                    tgt["repro"] = m["repro"]

        for i, m in enumerate(sorted(
                coalesced.values(), key=lambda x: -x["score"]), 1):
            bug_id = f"QA-{i:02d}"
            pb, repro = m["pb"], m["repro"]
            title = self._title(pb, m["elem"], m["route"])
            steps = self._human_steps(pb)
            expected, actual = self._expect_actual(pb)
            shot = pb.evidence.screenshot
            trace = f"trace_{pb.test_id}.zip"
            fb = FiledBug(
                bug_id=bug_id, title=title, severity=m["sev"], journey=pb.journey,
                route=m["route"], element_name=m["elem"],
                signals=[h.signal for h in m["signals"]], score=m["score"],
                reproduced_count=repro, steps_to_reproduce=steps,
                expected_behavior=expected, actual_behavior=actual,
                evidence={"screenshot": shot, "trace": trace,
                          "signals": [f"S{h.signal} {h.name}: {h.evidence[:160]}"
                                      for h in m["signals"]]})
            from .recommendations import advise
            md = self.jinja.get_template("bug_report.md.j2").render(
                bug=fb, recommendations=advise(fb))
            (reports_dir / f"{bug_id}.md").write_text(md, encoding="utf-8")
            filed.append(fb)
        (self.run_dir / "bugs_filed.json").write_text(
            json.dumps([b.model_dump() for b in filed], indent=2, ensure_ascii=False),
            encoding="utf-8")
        return filed

    # ------------------------------------------------------------ severity
    @staticmethod
    def _route_of(url: str) -> str:
        from urllib.parse import urlparse
        p = urlparse(url or "")
        return (re.sub(r"/\d+(?=/|$)", "/*", p.path) or "/").rstrip("/") or "/"

    @staticmethod
    def _element_of(pb: PossibleBug) -> str:
        g = pb.group
        # a broken item names the defendant precisely — but ONLY when the bug
        # is actually about that broken thing (every page shares the same
        # broken footer link; it must not own unrelated form bugs)
        if g.startswith(("http:", "broken:")):
            for b in pb.evidence.broken_items:
                if b.get("name"):
                    return b["name"]
        if g.startswith("intent:"):
            m = re.search(r"'([^']{3,40})'", g)
            if m:
                return m.group(1)
        # the interactive element acted upon at the failing step
        if 0 <= pb.step_index < len(pb.repro_steps):
            s = pb.repro_steps[pb.step_index]
            if s.action in ("click", "dblclick", "fill", "select", "press"):
                return s.name or s.role
        for s in pb.repro_steps:
            if s.action in ("click", "dblclick", "fill", "select", "press"):
                return s.name or s.role
        for v in pb.evidence.axe_violations:
            if v.get("target"):
                return f"@{v['target'][:40]}"
        return pb.evidence.target or "page"
    @staticmethod
    def _severity(pb: PossibleBug) -> str:
        blob = (pb.summary + " " + pb.journey).lower()
        sig = {h.signal for h in pb.signals}
        if "negative" in blob and "$" in blob or "price mismatch" in blob:
            return "Critical"
        if any("→ 5" in h.evidence for h in pb.signals) and \
                any(w in blob for w in ("promo", "pay", "checkout", "order")):
            return "Critical"
        if 4 in sig or "silently accepted" in blob or "$0.00" in blob \
                or "badge expected" in blob:
            return "Major"
        if 2 in sig and "404" in blob:
            return "Minor"
        if sig & {1, 3, 5}:
            return "Minor"
        return "Major"

    # ------------------------------------------------------------ prose
    def _title(self, pb: PossibleBug, elem: str, route: str) -> str:
        fallback = self._fallback_title(pb, elem, route)
        if not self.llm.available:
            return fallback
        system = ("You write one-line bug titles. Facts only, <= 110 chars, "
                  "no emoji. Reply as JSON {\"title\": ..., \"summary\": ...}.")
        user = f"Signals: {pb.summary}\nRoute: {route}\nElement: {elem}\nJourney: {pb.journey}"
        data = self.llm.call_json("reporter#4", system, user)
        if data and data.get("title"):
            return re.sub(r"\s+", " ", data["title"]).strip()[:140]
        return fallback

    @staticmethod
    def _fallback_title(pb: PossibleBug, elem: str, route: str) -> str:
        s0 = max(pb.signals, key=lambda h: h.weight) if pb.signals else None
        if not s0:
            return pb.journey
        ev = re.sub(r"\s+", " ", s0.evidence).strip()
        subject = elem if elem and elem != "page" else route
        return f"{subject}: {s0.name} — {ev[:88]}"[:140]

    @staticmethod
    def _human_steps(pb: PossibleBug) -> list[str]:
        out = []
        for s in pb.repro_steps:
            if s.action == "goto":
                out.append(f"Open `{s.target}`")
            elif s.action in ("click", "dblclick"):
                verb = "Click" if s.action == "click" else "Rapidly double-click"
                out.append(f'{verb} the {s.role} **"{s.name}"**')
            elif s.action == "fill":
                out.append(f'Type `{s.value}` into the {s.role} **"{s.name}"**')
            elif s.action == "press":
                out.append(f"Press **{s.target or 'Enter'}**")
            elif s.action == "check_text":
                out.append(f'Verify the text "{s.target}" is shown')
            else:
                out.append(f"{s.action} {s.target}")
        return [f"{i}. {t}" for i, t in enumerate(out, 1)]

    @staticmethod
    def _expect_actual(pb: PossibleBug) -> tuple[str, str]:
        signals = {hit.signal for hit in pb.signals}
        if 5 in signals and pb.evidence.axe_violations:
            violation = pb.evidence.axe_violations[0]
            expected = f"{violation.get('help') or 'The element should meet the applicable accessibility requirement.'}"
        elif 4 in signals:
            expected = "The observed form action should follow the stated application requirements."
        elif 2 in signals:
            expected = "A first-party document or API request required by this observed journey should complete successfully."
        elif 3 in signals:
            expected = "Visible images and links used by this page should load or lead to available content."
        elif 1 in signals:
            expected = "The page should not raise an uncaught JavaScript runtime error during this journey."
        else:
            expected = "The observed behavior should match the requirements and controls on this page."
        actual = f"{pb.summary} — observed during journey: {pb.journey}"
        return expected, actual
