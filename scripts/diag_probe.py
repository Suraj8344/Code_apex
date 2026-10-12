"""One-off diagnostic: BUG-02/03/07/11 detector gaps."""
import sys, json
sys.path.insert(0, "/home/user/qarena")
import httpx
httpx.get("http://localhost:8900/__seed__")

from core.sandbox import Sandbox
from core.tester import Tester
from core.judge import Judge
from core.llm import LLMPolicy
from core.contracts import TestCase, StepSpec, ExpectedContract
from core import config
from pathlib import Path

rd = Path("/tmp/qarena_diag"); rd.mkdir(exist_ok=True)
sbx = Sandbox("http://localhost:8900", rd)
sbx.start_browser(); sbx.login_once("demo@cloudcart.dev", "demo12345")
class _AM:
    email = None
    states = []
    catalog_prices = {"Neon Headphones": 4999}
class _T(Tester):
    def __init__(self, sbx): super().__init__(sbx, _AM(), rd, lambda e: None)
tester = _T(sbx)
judge = Judge(LLMPolicy())

def run(name, tc):
    sbx.seed_target()
    v, evs = tester.run_test(tc, tracing=False)
    print(f"\n##### {name} verdict={v}")
    for i, ev in enumerate(evs):
        print(f" step{i} {ev.action}:{ev.target} url={ev.url_after.replace('http://localhost:8900','')}")
        print("   http:", ev.http_failures, "\n   console:", ev.console_errors[:2],
              "\n   axe:", [(x['id'],x['impact']) for x in ev.axe_violations][:6],
              "\n   contract:", ev.contract_failures, "\n   intent:", ev.intent_findings[:2])
        for pb in judge.evaluate(name, "D", i, ev, tc.steps[:i+1]):
            print("   -> suspect:", pb.group, round(pb.score,2))

run("BUG-02 promo", TestCase(
    test_id="D1", journey_type="happy_path", title="promo 500",
    steps=[StepSpec(step=1, action="goto", target="/"),
           StepSpec(step=2, action="click", role="button", name="Add to Cart"),
           StepSpec(step=3, action="goto", target="/cart"),
           StepSpec(step=4, action="fill", role="textbox", name="Promo code", value="SAVE10"),
           StepSpec(step=5, action="click", role="button", name="Apply")],
    expected=ExpectedContract(text_required=["Promo"])))

run("BUG-03 product console", TestCase(
    test_id="D2", journey_type="happy_path", title="console",
    steps=[StepSpec(step=1, action="goto", target="/product/1"),
           StepSpec(step=2, action="click", role="button", name="Add to Cart")],
    expected=ExpectedContract(badge_equals=None)))

run("BUG-07 axe checkout", TestCase(
    test_id="D3", journey_type="happy_path", title="axe",
    steps=[StepSpec(step=1, action="goto", target="/"),
           StepSpec(step=2, action="click", role="button", name="Add to Cart"),
           StepSpec(step=3, action="goto", target="/checkout")],
    expected=ExpectedContract()))

run("BUG-11 dblclick", TestCase(
    test_id="D4", journey_type="edge_case_negative", title="dbl",
    steps=[StepSpec(step=1, action="goto", target="/"),
           StepSpec(step=2, action="dblclick", role="button", name="Add to Cart")],
    expected=ExpectedContract(badge_equals=1)))
