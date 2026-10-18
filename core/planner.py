"""
Stage 4 — evidence-grounded test planning.

The planner uses the observed AppMap and the user's stated requirements. It
must not infer that an application is a store, forum, LMS, or any other product
category from generic QA templates.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from . import config
from .contracts import AppMap, ExpectedContract, StepSpec, TestCase
from .llm import LLMPolicy
from .safety import is_safe_exploration_button


class Planner:
    def __init__(self, appmap: AppMap, llm: LLMPolicy) -> None:
        self.map = appmap
        self.llm = llm

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip().lower())

    @staticmethod
    def _route_key(route: str) -> str:
        path = urlsplit(route).path or "/"
        return re.sub(r"/\d+(?=/|$)", "/*", path).rstrip("/") or "/"

    @staticmethod
    def _target_for_state(state) -> str:
        parsed = urlsplit(state.url)
        return parsed.path + (("?" + parsed.query) if parsed.query else "") or "/"

    def _states_for_route(self, route: str):
        route_norm = self._route_key(route)
        return [state for state in self.map.states
                if self._route_key(state.route) == route_norm]

    def _observed_element(self, route: str, role: str, name: str) -> bool:
        wanted = self._norm(name)
        return any(element.role == role and self._norm(element.name) == wanted
                   for state in self._states_for_route(route)
                   for element in state.elements)

    def plan(self) -> tuple[list[TestCase], list[dict]]:
        cases = self.grammar_plan()
        if self.llm.available:
            cases.extend(self.llm_plan())
        valid, rejected = self.reject_invented(cases)
        valid, unsupported = self.reject_unsupported_expectations(valid)
        rejected.extend(unsupported)
        # Keep one test per title/route to avoid duplicate navigation checks.
        unique: list[TestCase] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for case in valid:
            key = (self._norm(case.title), tuple(step.target for step in case.steps
                                                  if step.action == "goto"))
            if key in seen:
                continue
            seen.add(key)
            case.priority_score = self.priority(case)
            unique.append(case)
        # Route smoke checks come first: an AI-selected journey must not crowd
        # discovered pages out of a comprehensive report.
        unique.sort(key=lambda item: (0 if item.title.startswith("Observed page ") else 1,
                                      -item.priority_score))
        return unique[:config.MAX_TESTS], rejected

    def llm_plan(self) -> list[TestCase]:
        states = [{"route": state.route, "status": state.http_status,
                   "title": state.title, "visible_summary": state.summary[:800],
                   "elements": [{"role": element.role, "name": element.name,
                                 "required": element.required,
                                 "enabled": element.enabled}
                                for element in state.elements[:35]]}
                  for state in self.map.states]
        system = (
            "Act as a careful QA engineer testing only the application described "
            "by the supplied AppMap and requirements. Return strict JSON with "
            "{\"cases\":[{test_id,journey_type,title,steps,expected}]}. Each step "
            "must use an allowed action: goto, click, dblclick, fill, press, "
            "select, check_text, assert_badge, or wait. A goto route must be "
            "successfully observed or linked from a successful page. Interactive "
            "role/name pairs must exactly match controls on the current page. "
            "Do not assume a product category or invent routes, controls, data, "
            "expected messages, or business rules. Use the description as the "
            "requirements oracle. Only assert behavior explicitly stated there or "
            "directly demonstrated by the UI. Prefer safe, reversible journeys; "
            "never submit payment, delete data, log out, create accounts, or send "
            "messages. An unobserved route returning 404 is not a bug.")
        user = ("Application requirements:\n" + self.map.description +
                "\n\nObserved AppMap:\n" + json.dumps(
                    {"states": states, "links": self.map.edges}, ensure_ascii=False)[:12000])
        data = self.llm.call_json("planner#1", system, user)
        if not data or not isinstance(data.get("cases"), list):
            return []
        cases: list[TestCase] = []
        for raw in data["cases"]:
            try:
                raw["steps"] = [StepSpec(**{key: value for key, value in step.items()
                                             if key in StepSpec.model_fields})
                                for step in raw.get("steps", [])]
                raw["expected"] = ExpectedContract(
                    **{key: value for key, value in raw.get("expected", {}).items()
                       if key in ExpectedContract.model_fields})
                cases.append(TestCase(**{key: value for key, value in raw.items()
                                         if key in ("test_id", "journey_type",
                                                    "priority_score", "title",
                                                    "steps", "expected")}))
                cases[-1].test_id = "AI_" + cases[-1].test_id.removeprefix("AI_")
            except Exception:
                continue
        return cases

    def grammar_plan(self) -> list[TestCase]:
        cases: list[TestCase] = []
        next_id = 0

        def add(title: str, route: str, steps: list[StepSpec] | None = None,
                kind: str = "happy_path") -> None:
            nonlocal next_id
            next_id += 1
            cases.append(TestCase(
                test_id=f"TC_{next_id:03d}", journey_type=kind, title=title,
                steps=steps or [StepSpec(step=1, action="goto", target=route)],
                expected=ExpectedContract(no_new_errors_5xx=True)))

        # Revisit pages discovered by crawling and capture fresh runtime
        # evidence. A found route is not a business requirement; this check
        # only confirms that the observed page still loads.
        successful_states = []
        seen_routes: set[str] = set()
        for state in self.map.states:
            route_key = self._route_key(state.route)
            if state.http_status == 200 and route_key not in seen_routes:
                successful_states.append(state)
                seen_routes.add(route_key)
        for state in successful_states:
            target = self._target_for_state(state)
            add(f"Observed page {state.route} loads without a server error", target)

        # Search is exercised only when the UI actually exposes a searchbox.
        for state in self.map.states:
            search = next((element for element in state.elements
                           if element.role == "searchbox" and element.enabled), None)
            if search:
                add(f"Observed search control on {state.route} accepts a query",
                    self._target_for_state(state),
                    [StepSpec(step=1, action="goto",
                              target=self._target_for_state(state)),
                     StepSpec(step=2, action="fill", role="searchbox",
                              name=search.name, value="QArena search probe"),
                     StepSpec(step=3, action="press", role="searchbox",
                              name=search.name, target="Enter")])

        # A dead route is actionable only if a successful page actually linked
        # to it. Direct probes and guessed URLs never enter this list.
        states_by_id = {state.state_id: state for state in self.map.states}
        for edge in self.map.edges:
            source = states_by_id.get(edge.get("from"))
            match = re.fullmatch(r'button "(.*)"', edge.get("via", ""))
            if (source and source.http_status == 200 and match
                    and is_safe_exploration_button(match.group(1))):
                label = match.group(1)
                target = self._target_for_state(source)
                add(f'Observed control "{label}" on {source.route} responds', target,
                    [StepSpec(step=1, action="goto", target=target),
                     StepSpec(step=2, action="click_check_response", role="button", name=label)])

        linked_routes: set[str] = set()
        for edge in self.map.edges:
            source = states_by_id.get(edge.get("from"))
            if source and source.http_status == 200:
                linked_routes.add(self._route_key(edge.get("to", "")))
        for state in self.map.states:
            if state.http_status >= 400 and self._route_key(state.route) in linked_routes:
                add(f"Observed site link leads to unavailable page {state.route}",
                    self._target_for_state(state))
        return cases

    def reject_unsupported_expectations(
            self, cases: list[TestCase]) -> tuple[list[TestCase], list[dict]]:
        """Strip model assertions that cannot be tied to requirements or UI evidence."""
        proof = self._norm(" ".join([self.map.description] + [
            element.name for state in self.map.states for element in state.elements]))
        kept: list[TestCase] = []
        notes: list[dict] = []
        for case in cases:
            removed: list[str] = []
            expected = case.expected
            for field in ("text_required", "text_forbidden"):
                values = getattr(expected, field)
                grounded = [value for value in values if self._norm(value) in proof]
                if len(grounded) != len(values):
                    removed.extend(value for value in values if value not in grounded)
                    setattr(expected, field, grounded)
            if expected.error_text and self._norm(expected.error_text) not in proof:
                removed.append(expected.error_text)
                expected.error_text = ""
            if expected.success_url_contains and not any(
                    expected.success_url_contains.lower() in state.route.lower()
                    for state in self.map.states if state.http_status == 200):
                removed.append("success URL " + expected.success_url_contains)
                expected.success_url_contains = ""
            if expected.should_reject and not any(word in proof for word in (
                    "reject", "invalid", "required", "validation", "must not", "cannot")):
                removed.append("unsupported rejection expectation")
                expected.should_reject = False
            if expected.badge_equals is not None and not any(
                    word in proof for word in ("badge", "cart", "item count")):
                removed.append("unsupported badge assertion")
                expected.badge_equals = None
            if expected.money_non_negative and not any(
                    word in proof for word in ("price", "money", "amount", "total", "cost")):
                removed.append("unsupported money assertion")
                expected.money_non_negative = False
            if removed:
                notes.append({"test_id": case.test_id, "title": case.title,
                              "removed_unsupported_expectations": removed})
            kept.append(case)
        return kept, notes

    def reject_invented(self, cases: list[TestCase]) -> tuple[list[TestCase], list[dict]]:
        valid: list[TestCase] = []
        rejected: list[dict] = []
        good_routes = {self._route_key(state.route) for state in self.map.states
                       if state.http_status == 200}
        linked_routes: set[str] = set()
        states_by_id = {state.state_id: state for state in self.map.states}
        for edge in self.map.edges:
            source = states_by_id.get(edge.get("from"))
            if source and source.http_status == 200:
                linked_routes.add(self._route_key(edge.get("to", "")))
        allowed_routes = good_routes | linked_routes | {"/"}

        for case in cases:
            route = "/"
            invalid: list[str] = []
            for step in case.steps:
                if step.action == "goto":
                    raw_target = step.target.strip()
                    if not raw_target.startswith("/") or raw_target.startswith("//"):
                        invalid.append(f"goto must be a same-site path: {step.target}")
                        continue
                    route = self._route_key(step.target)
                    if route not in allowed_routes:
                        invalid.append(f"unobserved route {step.target}")
                elif step.action in ("click", "click_check_response", "fill", "select", "dblclick", "press"):
                    if not self._observed_element(route, step.role, step.name):
                        invalid.append(f'{step.action} {step.role} "{step.name}" on {route}')
            if invalid:
                rejected.append({"test_id": case.test_id, "title": case.title,
                                 "invented_steps": invalid})
            else:
                valid.append(case)
        return valid, rejected

    @staticmethod
    def priority(case: TestCase) -> int:
        score = 50
        if case.journey_type.startswith("edge"):
            score += 15
        if case.journey_type == "adversarial":
            score += 20
        if "unavailable page" in case.title:
            score += 20
        return min(score, 99)
