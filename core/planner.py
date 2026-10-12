"""
Stage 4 — Planner Agent: test case synthesis & guardrails.
[LLM #1: LiteLLM + Pydantic] when a key is configured; otherwise the
deterministic journey-grammar generator (works key-free).
[Python Check vs AppMap] REJECT INVENTED STEPS  |  [Python Priority Score]
"""
from __future__ import annotations

import re

from . import config
from .contracts import AppMap, ExpectedContract, StepSpec, TestCase
from .llm import LLMPolicy

DESCRIPTION_WORDS = ("sign up", "signup", "log in", "login", "search", "cart",
                     "promo", "coupon", "checkout", "order", "pay")


class Planner:
    def __init__(self, appmap: AppMap, llm: LLMPolicy) -> None:
        self.map = appmap
        self.llm = llm
        self.element_index = self._index_elements()
        self.products = [n for n in appmap.catalog_prices.keys()
                         if len(n) > 2][:8] or ["Quantum Mug"]

    # ------------------------------------------------------- element index
    def _index_elements(self) -> dict[str, set[str]]:
        idx: dict[str, set[str]] = {}
        for s in self.map.states:
            for e in s.elements:
                idx.setdefault(e.role, set()).add(self._norm(e.name))
        return idx

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip().lower())

    def has(self, role: str, name_frag: str) -> bool:
        if role == "route":
            return any(self._norm(name_frag) in self._norm(s.route)
                       for s in self.map.states)
        frag = self._norm(name_frag)
        return any(frag in n for n in self.element_index.get(role, set()))

    def route_of(self, frag: str, default: str) -> str:
        f = self._norm(frag)
        for s in self.map.states:
            if self._norm(f) in self._norm(s.route) and s.http_status == 200:
                return s.route.replace("/*", "/1")
        return default

    # ---------------------------------------------------------------- main
    def plan(self) -> tuple[list[TestCase], list[dict]]:
        cases = self.llm_plan() if self.llm.available else []
        if not cases:
            cases = self.grammar_plan()
        valid, rejected = self.reject_invented(cases)
        for tc in valid:
            tc.priority_score = self.priority(tc)
        valid.sort(key=lambda t: t.priority_score, reverse=True)
        return valid[: config.MAX_TESTS], rejected

    # ------------------------------------------------- LLM #1 (optional)
    def llm_plan(self) -> list[TestCase]:
        states = [{"route": s.route, "status": s.http_status,
                   "elements": [{"role": e.role, "name": e.name}
                                for e in s.elements[:22]]}
                   for s in self.map.states]
        system = (
            "You are a senior QA engineer. Produce adversarial AND happy-path "
            "browser test cases as strict JSON: {\"cases\":[{test_id, "
            "journey_type, title, steps:[{step, action, target, role, name, "
            "value}], expected:{success_url_contains, disallowed_url, "
            "text_required, text_forbidden, should_reject, error_text, "
            "badge_equals, money_non_negative}}]}. Rules: actions in "
            "goto|click|dblclick|fill|press|select|check_text|assert_badge|wait. "
            "goto target = route path. click/fill use role+name exactly as "
            "listed. Cover boundary values (0, -1, 11), invalid emails, empty "
            "required fields, double-clicks, empty-cart checkout, promo abuse.")
        user = ("App description:\n" + self.map.description +
                "\n\nAppMap JSON:\n" + __import__("json").dumps(states)[:9000])
        data = self.llm.call_json("planner#1", system, user)
        if not data or "cases" not in data:
            return []
        out: list[TestCase] = []
        try:
            for raw in data["cases"]:
                raw["steps"] = [StepSpec(**{k: v for k, v in st.items()
                                            if k in StepSpec.model_fields})
                                for st in raw.get("steps", [])]
                raw["expected"] = ExpectedContract(
                    **{k: v for k, v in raw.get("expected", {}).items()
                       if k in ExpectedContract.model_fields})
                out.append(TestCase(**{k: v for k, v in raw.items()
                                       if k in ("test_id", "journey_type",
                                                "priority_score", "title",
                                                "steps", "expected")}))
        except Exception:
            return []
        return out

    # ------------------------------------- deterministic journey grammar
    def grammar_plan(self) -> list[TestCase]:
        T: list[TestCase] = []
        uid = [0]

        def nid() -> str:
            uid[0] += 1
            return f"TC_{uid[0]:03d}"

        prod = self.products[0]

        # --- auth journeys ---------------------------------------------------
        if self.has("route", "signup"):
            T.append(TestCase(
                test_id=nid(), journey_type="happy_path", title="Sign up new account",
                steps=[StepSpec(step=1, action="goto", target="/signup"),
                       StepSpec(step=2, action="fill", role="textbox", name="Full name", value="Trial User"),
                       StepSpec(step=3, action="fill", role="textbox", name="Email", value="trial@qarena.dev"),
                       StepSpec(step=4, action="fill", role="textbox", name="Password", value="passw0rd!"),
                       StepSpec(step=5, action="click", role="button", name="Create account")],
                expected=ExpectedContract(text_required=["Add to Cart"])))
            T.append(TestCase(
                test_id=nid(), journey_type="edge_case_negative",
                title="Sign-up rejects invalid email",
                steps=[StepSpec(step=1, action="goto", target="/signup"),
                       StepSpec(step=2, action="fill", role="textbox", name="Full name", value="Bad Mail"),
                       StepSpec(step=3, action="fill", role="textbox", name="Email", value="not-an-email"),
                       StepSpec(step=4, action="fill", role="textbox", name="Password", value="passw0rd!"),
                       StepSpec(step=5, action="click", role="button", name="Create account")],
                expected=ExpectedContract(should_reject=True, error_text="valid email",
                                          disallowed_url="/")))
        if self.has("route", "login"):
            T.append(TestCase(
                test_id=nid(), journey_type="happy_path", title="Log in with valid credentials",
                steps=[StepSpec(step=1, action="goto", target="/login"),
                       StepSpec(step=2, action="fill", role="textbox", name="Email", value="demo@cloudcart.dev"),
                       StepSpec(step=3, action="fill", role="textbox", name="Password", value="demo12345"),
                       StepSpec(step=4, action="click", role="button", name="Log in")],
                expected=ExpectedContract(text_required=["Log out"])))

        # --- search intent ---------------------------------------------------
        if self.has("searchbox", ""):
            T.append(TestCase(
                test_id=nid(), journey_type="happy_path",
                title=f"Search finds exact product name '{prod}'",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="fill", role="searchbox", name="Search", value=prod),
                       StepSpec(step=3, action="press", role="searchbox", name="Search", target="Enter")],
                expected=ExpectedContract(text_required=[prod],
                                          text_forbidden=["No products found"])))

        # --- cart / promo / quantity adversarial -----------------------------
        can_cart = self.has("button", "add to cart")
        if can_cart:
            T.append(TestCase(
                test_id=nid(), journey_type="happy_path",
                title=f"Add '{prod}' to cart — badge shows 1",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="click", role="button", name="Add to Cart")],
                expected=ExpectedContract(badge_equals=1)))
            T.append(TestCase(
                test_id=nid(), journey_type="adversarial",
                title="Rapid double-click adds exactly one unit",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="dblclick", role="button", name="Add to Cart")],
                expected=ExpectedContract(badge_equals=1)))
            T.append(TestCase(
                test_id=nid(), journey_type="edge_case_negative",
                title="Cart rejects negative quantity (-5)",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="click", role="button", name="Add to Cart"),
                       StepSpec(step=3, action="goto", target="/cart"),
                       StepSpec(step=4, action="fill", role="spinbutton", name="Quantity", value="-5"),
                       StepSpec(step=5, action="click", role="button", name="Update")],
                expected=ExpectedContract(should_reject=True,
                                          error_text="between 1 and 10",
                                          money_non_negative=True)))
            T.append(TestCase(
                test_id=nid(), journey_type="adversarial",
                title="Negative quantity must never produce a negative money total",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="click", role="button", name="Add to Cart"),
                       StepSpec(step=3, action="goto", target="/cart"),
                       StepSpec(step=4, action="fill", role="spinbutton", name="Quantity", value="-5"),
                       StepSpec(step=5, action="click", role="button", name="Update"),
                       StepSpec(step=6, action="goto", target="/checkout"),
                       StepSpec(step=7, action="fill", role="textbox", name="Full name", value="Neg Tester"),
                       StepSpec(step=8, action="fill", role="textbox", name="Email", value="neg@qarena.dev"),
                       StepSpec(step=9, action="fill", role="textbox", name="Street address", value="1 Bug Lane"),
                       StepSpec(step=10, action="click", role="button", name="Pay Now")],
                # NOT should_reject: on a correct app the -5 is rejected at
                # Update and the remaining positive cart may legitimately be
                # paid. The true invariant: money must never go negative.
                expected=ExpectedContract(money_non_negative=True)))
        # --- product detail page console probe -------------------------------
        if self.has("route", "product"):
            prod_route = self.route_of("product", "/product/1")
            T.append(TestCase(
                test_id=nid(), journey_type="happy_path",
                title="Product page: Add to Cart with a clean console",
                steps=[StepSpec(step=1, action="goto", target=prod_route),
                       StepSpec(step=2, action="click", role="button", name="Add to Cart")],
                expected=ExpectedContract(badge_equals=None)))

        if self.has("textbox", "promo"):
            T.append(TestCase(
                test_id=nid(), journey_type="happy_path",
                title="Promo SAVE10 applies a 10% discount",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="click", role="button", name="Add to Cart"),
                       StepSpec(step=3, action="goto", target="/cart"),
                       StepSpec(step=4, action="fill", role="textbox", name="Promo code", value="SAVE10"),
                       StepSpec(step=5, action="click", role="button", name="Apply")],
                expected=ExpectedContract(text_required=["Promo"], no_new_errors_5xx=True)))
            T.append(TestCase(
                test_id=nid(), journey_type="edge_case_negative",
                title="Unknown promo code fails gracefully",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="click", role="button", name="Add to Cart"),
                       StepSpec(step=3, action="goto", target="/cart"),
                       StepSpec(step=4, action="fill", role="textbox", name="Promo code", value="XXX-NOPE"),
                       StepSpec(step=5, action="click", role="button", name="Apply")],
                expected=ExpectedContract(should_reject=True,
                                          error_text="Invalid promo code")))

        # --- checkout ---------------------------------------------------------
        if self.has("route", "checkout"):
            T.append(TestCase(
                test_id=nid(), journey_type="edge_case_negative",
                title="Checkout blocks empty cart",
                steps=[StepSpec(step=1, action="goto", target="/checkout"),
                       StepSpec(step=2, action="click", role="button", name="Pay Now")],
                expected=ExpectedContract(should_reject=True,
                                          disallowed_url="/order",
                                          text_forbidden=["Order confirmed"],
                                          error_text="cart is empty")))
            T.append(TestCase(
                test_id=nid(), journey_type="edge_case_negative",
                title="Checkout requires shipping address",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="click", role="button", name="Add to Cart"),
                       StepSpec(step=3, action="goto", target="/checkout"),
                       StepSpec(step=4, action="fill", role="textbox", name="Full name", value="No Addr"),
                       StepSpec(step=5, action="fill", role="textbox", name="Email", value="noaddr@qarena.dev"),
                       StepSpec(step=6, action="click", role="button", name="Pay Now")],
                expected=ExpectedContract(should_reject=True, error_text="address is required",
                                          text_forbidden=["Order confirmed"])))
            T.append(TestCase(
                test_id=nid(), journey_type="happy_path",
                title=f"Full purchase journey: {prod}",
                steps=[StepSpec(step=1, action="goto", target="/"),
                       StepSpec(step=2, action="click", role="button", name="Add to Cart"),
                       StepSpec(step=3, action="goto", target="/checkout"),
                       StepSpec(step=4, action="fill", role="textbox", name="Full name", value="Happy Buyer"),
                       StepSpec(step=5, action="fill", role="textbox", name="Email", value="happy@qarena.dev"),
                       StepSpec(step=6, action="fill", role="textbox", name="Street address", value="42 Green Rd"),
                       StepSpec(step=7, action="click", role="button", name="Pay Now")],
                expected=ExpectedContract(success_url_contains="/order",
                                          text_required=["Order confirmed"],
                                          money_non_negative=True)))

        # --- dead-link patrol from exploration facts ---------------------------
        dead = [s for s in self.map.states if s.http_status >= 400]
        for ds in dead[:2]:
            link_name = ds.route.strip("/") or ds.route
            # prefer a real link click when an accessible link exists in the
            # element index — but icon-only/aria-less footers still deserve
            # the patrol: navigate to the (proven dead) route directly
            if self.has("link", link_name):
                steps = [StepSpec(step=1, action="goto", target="/"),
                         StepSpec(step=2, action="click", role="link",
                                  name=link_name.title().replace("-", " "))]
            else:
                steps = [StepSpec(step=1, action="goto", target="/"),
                         StepSpec(step=2, action="goto", target=ds.route)]
            T.append(TestCase(
                test_id=nid(), journey_type="happy_path",
                title=f"{'Footer link' if steps[-1].action == 'click' else 'Route'} '{link_name}' serves a real page",
                steps=steps,
                expected=ExpectedContract(no_new_errors_5xx=True)))
        return T

    # --------------------------------------------- invented-step rejection
    def reject_invented(self, cases: list[TestCase]) -> tuple[list[TestCase], list[dict]]:
        valid, rejected = [], []
        routes = {"/"} | {s.route for s in self.map.states}
        for tc in cases:
            ok, bad_steps = True, []
            for st in tc.steps:
                if st.action == "goto":
                    pat = re.sub(r"/\d+(?=/|$)", "/*", st.target)
                    if st.target not in routes and pat not in routes:
                        ok = False
                        bad_steps.append(f"goto {st.target}")
                elif st.action in ("click", "fill", "select", "dblclick", "press"):
                    if not self.has(st.role, st.name):
                        ok = False
                        bad_steps.append(f'{st.action} {st.role} "{st.name}"')
            (valid if ok else rejected).append(
                tc if ok else {"test_id": tc.test_id, "title": tc.title,
                               "invented_steps": bad_steps})
        return valid, rejected

    # -------------------------------------------------------------- priority
    @staticmethod
    def priority(tc: TestCase) -> int:
        score = 50
        blob = (tc.title + " " + " ".join(s.target for s in tc.steps)).lower()
        if tc.journey_type.startswith("edge"):
            score += 15
        if tc.journey_type == "adversarial":
            score += 25
        if "checkout" in blob or "pay now" in blob:
            score += 30
        if "login" in blob or "sign" in blob:
            score += 10
        if "promo" in blob:
            score += 10
        return min(score, 99)
