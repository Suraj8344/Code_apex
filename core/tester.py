"""
Stage 5a — Tester Agent: run each step, capture proof.
[Playwright get_by_role] element resolution chain
[Playwright event hooks] console / pageerror / response listeners
[Playwright tracing] screenshots + trace.zip
[Playwright assertions + Python] ExpectedContract evaluation
Deterministic intent heuristics (feeds Signal 6): negative-money scan,
$0-order scan, price-vs-catalog mismatch, badge truth.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config
from .contracts import AppMap, ExpectedContract, StepEvidence, StepSpec, TestCase
from .sandbox import Sandbox

NEG_MONEY_RE = re.compile(r"[-−–]\s?\$\s?\d|\$\s?[-−–]\s?\d")
PRICE_OF_RE = re.compile(r"([A-Z][A-Za-z0-9&'’\- ]{2,40}?)[^\n$]{0,40}\$(\d{1,5})\.(\d{2})")


class Tester:
    def __init__(self, sandbox: Sandbox, appmap: AppMap, run_dir: Path,
                 emit=lambda e: None) -> None:
        self.sbx = sandbox
        self.map = appmap
        self.run_dir = run_dir
        self.axe_src = config.AXE_JS.read_text()
        self.axe_injected: dict[int, bool] = {}  # per-page axe injection guard
        self.emit = emit
        self.link_status: dict[str, int] = {
            s.route: s.http_status for s in appmap.states}

    # ------------------------------------------------------------- per test
    def run_test(self, tc: TestCase, tracing: bool = True) -> tuple[str, list[StepEvidence]]:
        """Executes one TestCase. Returns (verdict, evidence-per-step)."""
        evidences: list[StepEvidence] = []
        ctx = self.sbx.new_context(with_auth=True, tracing=tracing)
        page = ctx.new_page()
        console_errors: list[str] = []
        http_failures: list[dict] = []

        def _console(m):
            if m.type != "error":
                return
            # Browser reports these for failed images/scripts/stylesheets too;
            # those requests are classified separately and are not by
            # themselves evidence that a user workflow is broken.
            if m.text.startswith("Failed to load resource:"):
                return
            try:
                loc = (m.location or {})
                path = " {" + (loc.get("url") or "").replace(self.sbx.target_url, "") + "}" \
                    if loc.get("url") else ""
            except Exception:
                path = ""
            console_errors.append(m.text + path)

        page.on("console", _console)
        page.on("pageerror", lambda e: console_errors.append("[uncaught] " + str(e)))
        page.on("response", lambda r: http_failures.append(
            {"method": r.request.method, "url": r.url, "status": r.status,
             "resource_type": r.request.resource_type})
            if r.status >= 400 and self.sbx.base_host in r.url else None)

        verdict = "passed"
        steps_dir = self.run_dir / "steps"
        steps_dir.mkdir(exist_ok=True)
        for i, spec in enumerate(tc.steps):
            ev, stop = self._run_one_step(page, spec, tc, i, console_errors,
                                          http_failures, steps_dir)
            evidences.append(ev)
            self.emit({"type": "step", "test": tc.test_id, "step": spec.step,
                       "action": spec.action, "status": ev.status})
            if ev.status == "error":
                verdict = "error"
            if stop:
                break
        if verdict != "error" and any(
                e.contract_failures or e.status != "passed" for e in evidences):
            verdict = "failed"
        if tracing:
            try:
                ctx.tracing.stop(path=str(self.run_dir / f"trace_{tc.test_id}.zip"))
            except Exception:
                pass
        ctx.close()
        return verdict, evidences

    def _run_one_step(self, page, spec: StepSpec, tc: TestCase, i: int,
                      console_errors: list, http_failures: list,
                      steps_dir: Path) -> tuple[StepEvidence, bool]:
        """Returns (evidence, stop_after_this_step)."""
        ev = StepEvidence(step=spec.step, action=spec.action,
                          target=spec.target or spec.name)
        ev.url_before = page.url
        c0, h0 = len(console_errors), len(http_failures)
        try:
            ok, note = self._do_step(page, spec, tc)
            if not ok and tc.expected.should_reject and note.startswith("unresolved"):
                # negative test: the control refusing interaction IS the rejection
                ev.status = "passed"
                ev.note = "control absent — app refuses the action (rejection satisfied)"
                self._capture(page, ev, spec, tc, i, console_errors, http_failures,
                              c0, h0, steps_dir)
                return ev, True
            ev.status = "passed" if ok else "failed"
            ev.note = note
        except Exception as exc:
            ev.status = "error"
            ev.note = f"{exc.__class__.__name__}: {str(exc)[:140]}"
        self._capture(page, ev, spec, tc, i, console_errors, http_failures,
                      c0, h0, steps_dir)
        return ev, ev.status == "error"

    def _capture(self, page, ev: StepEvidence, spec: StepSpec, tc: TestCase,
                 i: int, console_errors: list, http_failures: list,
                 c0: int, h0: int, steps_dir: Path) -> None:
        ev.url_after = page.url
        ev.console_errors = console_errors[c0:]
        ev.http_failures = http_failures[h0:]
        try:
            ev.broken_items = self._dom_scan(page)
            ev.axe_violations = self._axe_scan(page)
            ev.intent_findings = self._intent_scan(page, tc.expected)
            ev.contract_failures = self._eval_contract(
                page, tc.expected, is_final=(i == len(tc.steps) - 1))
        except Exception:
            pass
        shot = steps_dir / f"{tc.test_id}_step{spec.step}.png"
        try:
            page.screenshot(path=str(shot))
            ev.screenshot = str(shot.relative_to(self.run_dir))
        except Exception:
            pass

    # -------------------------------------------------------------- actions
    def _do_step(self, page, spec: StepSpec, tc: TestCase) -> tuple[bool, str]:
        a = spec.action
        if a == "goto":
            resp = page.goto(self.sbx.target_url + spec.target,
                             wait_until="domcontentloaded")
            return True, f"HTTP {resp.status if resp else '?'}"
        if a == "wait":
            page.wait_for_timeout(int(spec.target or "600"))
            return True, ""
        el = self._resolve(page, spec.role, spec.name)
        if el is None:
            return False, f'unresolved {spec.role} "{spec.name}"'
        if a == "click":
            el.click()
            page.wait_for_timeout(450)
        elif a == "dblclick":
            el.dblclick()
            page.wait_for_timeout(450)
        elif a == "fill":
            el.fill(spec.value)
        elif a == "press":
            el.press(spec.target or "Enter")
            page.wait_for_timeout(450)
        elif a == "select":
            el.select_option(spec.value)
        elif a == "check_text":
            txt = page.inner_text("body")
            if spec.target.lower() not in txt.lower():
                return False, f'text missing: "{spec.target}"'
        elif a == "assert_badge":
            return self._check_badge(page, int(spec.target or "1"))
        return True, ""

    def _resolve(self, page, role: str, name: str):
        """get_by_role chain with graceful fallbacks (deterministic, no LLM)."""
        try:
            el = page.get_by_role(role, name=name, exact=False)  # type: ignore[arg-type]
            if el.count():
                return el.first
        except Exception:
            pass
        fallbacks = [lambda: page.get_by_label(name),
                     lambda: page.get_by_placeholder(name),
                     lambda: page.get_by_text(name, exact=False)]
        for fb in fallbacks:
            try:
                el = fb()
                if el.count():
                    return el.first
            except Exception:
                continue
        return None

    def _check_badge(self, page, expected: int) -> tuple[bool, str]:
        for _ in range(6):
            try:
                b = page.locator("#cart-badge")
                if b.count() and b.inner_text().strip().lstrip("-").isdigit():
                    val = int(b.inner_text().strip())
                    if val == expected:
                        return True, ""
                    return False, f"badge expected {expected}, saw {val}"
            except Exception:
                pass
            page.wait_for_timeout(250)
        return False, "badge not found"

    # ------------------------------------------------------------- scanners
    def _dom_scan(self, page) -> list[dict]:
        """Signal 3 raw facts: broken images + links to known-dead routes."""
        out: list[dict] = []
        try:
            imgs = page.eval_on_selector_all(
                "img", "els => els.map(i => [i.naturalWidth, i.alt, i.currentSrc])")
            for nw, alt, src in imgs:
                if nw == 0:
                    out.append({"kind": "img", "name": alt, "detail": src})
        except Exception:
            pass
        try:
            hrefs = page.eval_on_selector_all(
                "a[href]", "els => els.map(a => [a.getAttribute('href'),"
                           " (a.innerText||'').trim()])")
            seen = set()
            for href, label in hrefs:
                if not href:
                    continue
                route = re.sub(r"/\d+(?=/|$)", "/*", href.split("?")[0])
                if route in seen:
                    continue
                seen.add(route)
                if self.link_status.get(route, 200) >= 400:
                    out.append({"kind": "link", "name": label,
                                "detail": f"{href} → {self.link_status[route]}"})
        except Exception:
            pass
        return out[:12]

    def _axe_scan(self, page) -> list[dict]:
        try:
            if not self.axe_injected.get(id(page)):
                page.add_script_tag(content=self.axe_src)
                self.axe_injected[id(page)] = True
            res = page.evaluate(
                "async() => await axe.run(document, {resultTypes:['violations']})")
            out = []
            for v in res.get("violations", []):
                for node in v.get("nodes", [])[:3]:
                    tgt = (node.get("target") or ["?"])[0]
                    out.append({"id": v.get("id", "?"),
                                "impact": v.get("impact", "minor"),
                                "target": tgt[:90],
                                "help": (v.get("help") or "")[:160]})
            out.extend(self._placeholder_audit(page))
            return out[:10]
        except Exception:
            return []

    @staticmethod
    def _placeholder_audit(page) -> list[dict]:
        """Inputs named ONLY by placeholder = no programmatic label.
        axe's accName math still grants a placeholder name, so vanilla axe
        passes these — but WCAG 3.3.2/4.1.2 treat it as a violation."""
        try:
            bad = page.evaluate(
                """() => Array.from(document.querySelectorAll(
                       'input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,select'))
                    .filter(e => {
                       const labelled = (e.labels && e.labels.length > 0) ||
                           e.getAttribute('aria-label') || e.getAttribute('aria-labelledby');
                       return !labelled && e.getAttribute('placeholder');
                   }).map(e => (e.name || e.placeholder || '?'))""")
            return [{"id": "a11y-placeholder-only-label", "impact": "serious",
                     "target": f"input[name={n}]",
                     "help": f"Field '{n}' relies on placeholder as its only label"}
                    for n in bad]
        except Exception:
            return []

    def _intent_scan(self, page, expected: ExpectedContract) -> list[str]:
        """Deterministic Signal 6 facts (LLM #2 may add nuance, not needed to win)."""
        out: list[str] = []
        try:
            text = page.inner_text("body")
        except Exception:
            return out
        if NEG_MONEY_RE.search(text):
            m = NEG_MONEY_RE.search(text)
            out.append(f"Negative money amount visible: '{m.group(0)}' — money can never be negative per the app description")
        if "$0.00" in text and re.search(r"no items|order confirmed", text, re.I) \
                and "confirmed" in text.lower():
            out.append("$0.00 order confirmed with no items — contradicts checkout description")
        # price-vs-catalog mismatch on this page (same extraction as explorer)
        from .prices import extract_pairs
        seen_names: set[str] = set()
        for name, cents in extract_pairs(page):
            cat = self.map.catalog_prices.get(name)
            if cat is not None and cents != cat and name not in seen_names:
                seen_names.add(name)
                out.append(f"Price mismatch: '{name}' shows ${cents/100:.2f} here but the catalog lists ${cat/100:.2f}")
        return out

    # ------------------------------------------------------------- contract
    def _eval_contract(self, page, ex: ExpectedContract, is_final: bool) -> list[str]:
        out: list[str] = []
        url = page.url
        try:
            text = page.inner_text("body")
        except Exception:
            text = ""
        low = text.lower()

        if is_final and ex.disallowed_url and ex.disallowed_url in url:
            kind = "form:" if ex.should_reject else "flow:"
            out.append(f"{kind}reached disallowed URL '{ex.disallowed_url}'")
        if ex.should_reject and is_final:
            # an empty-cart order page isn't a "silently accepted" case —
            # BUG-08's own evidence class covers it; don't double-count
            empty_order = ("order confirmed" in low and "$0.00" in low
                           and "no items" in low)
            if not empty_order:
                needle = (ex.error_text or "error").lower()
                if needle not in low and "error" not in low and "invalid" not in low \
                        and "required" not in low and "empty" not in low:
                    # if the page simultaneously shows negative money, this is
                    # the SAME root-cause defect the money family reports —
                    # reclassify so it (a) dedupes against it and (b) doesn't
                    # file a second report for one defect (BUG-01 case)
                    if "-$" in text or "- $" in text:
                        out.append("form:money went negative on page")
                    else:
                        out.append("form:invalid input was silently accepted — no validation message shown")
        if ex.error_text and is_final and ex.should_reject is False:
            pass
        if is_final and ex.text_required:
            for t in ex.text_required:
                if t.lower() not in low:
                    out.append(f'expect:required text missing: "{t}"')
        if is_final and ex.text_forbidden:
            for t in ex.text_forbidden:
                if t.lower() in low:
                    kind = "form:" if ex.should_reject else "expect:"
                    out.append(f'{kind}forbidden text present: "{t}"')
        if ex.success_url_contains and is_final \
                and ex.success_url_contains not in url:
            out.append(f"flow:expected URL to contain '{ex.success_url_contains}', got {url}")
        if ex.money_non_negative and NEG_MONEY_RE.search(text):
            out.append("form:money went negative on page")
        if ex.badge_equals is not None and is_final:
            ok, msg = self._check_badge(page, ex.badge_equals)
            if not ok:
                out.append("flow:" + msg)
        return out
