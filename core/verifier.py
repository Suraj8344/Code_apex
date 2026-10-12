"""
Stage 6a — Deterministic 3x Replay Verifier (zero AI).
[Playwright: new_context] clean browser   [Playwright + JSON] replay exact steps
[Python Loop] repeat 3x (world re-seeded each attempt when the hook exists)
[Python Counter] >=2/3 => CONFIRMED, else => FLAKY
Oracle: the same evidence-group re-triggers on replay. No LLM anywhere.
"""
from __future__ import annotations

from . import config
from .contracts import ExpectedContract, PossibleBug, TestCase
from .judge import Judge
from .tester import Tester


class Verifier:
    def __init__(self, tester: Tester, judge: Judge, emit=lambda e: None) -> None:
        self.tester = tester
        self.judge = judge
        self.emit = emit

    def verify(self, pb: PossibleBug) -> tuple[bool, str]:
        original = {h.signal for h in pb.signals}
        # replay the ORIGINAAL contract: form/intent oracles are contract-driven
        expected = (ExpectedContract(**pb.oracle[0]) if pb.oracle
                    else ExpectedContract())
        repro_tc = TestCase(test_id="REPRO", journey_type="replay",
                            title=pb.journey, steps=pb.repro_steps,
                            expected=expected)
        hits = 0
        for attempt in range(1, config.REPLAY_TIMES + 1):
            self.tester.sbx.seed_target()
            try:
                _, evidences = self.tester.run_test(repro_tc, tracing=False)
            except Exception:
                continue
            matched = False
            for i, ev in enumerate(evidences):
                for again in self.judge.evaluate(pb.journey, "REPRO", i, ev,
                                                 pb.repro_steps):
                    if again.group == pb.group or \
                            ({h.signal for h in again.signals} & original and
                             again.group.split(":")[0] == pb.group.split(":")[0]):
                        matched = True
            if matched:
                hits += 1
            self.emit({"type": "replay", "journey": f"{pb.test_id} {pb.group}",
                       "attempt": attempt, "matched": matched})
        return hits >= config.REPLAY_CONFIRM_MIN, f"{hits}/{config.REPLAY_TIMES}"
