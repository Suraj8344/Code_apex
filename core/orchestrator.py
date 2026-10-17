"""
QArena Orchestrator — wires the 8-stage / 36-step master pipeline:
Stage 2 sandbox -> 3b explore -> 4 plan -> 5a/5b execute+judge (per test)
-> 6a replay-verify (per suspect) -> 7a report -> 7b export+self-verify
-> 8 benchmark score. Emits SSE events the whole way. Zero AI outside the
4 sanctioned call sites; everything else is Playwright + plain Python.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from . import coordination
from .contracts import (FiledBug, FlakySuspect, PossibleBug, RunResult,
                        TestCase)
from .explorer import Explorer
from .exporter import Exporter
from .judge import Judge
from .llm import LLMPolicy
from .planner import Planner
from .reporter import Reporter
from .sandbox import Sandbox
from .tester import Tester
from .verifier import Verifier


@dataclass
class RunHandle:
    run_id: str
    events: "queue.Queue[dict]" = field(default_factory=queue.Queue)
    history: list[dict] = field(default_factory=list)
    result: RunResult | None = None
    run_dir: Path | None = None
    thread: threading.Thread | None = None


class Orchestrator:
    def __init__(self) -> None:
        self.runs: dict[str, RunHandle] = {}

    # ------------------------------------------------------------ lifecycle
    def start_run(self, target_url: str, description: str,
                  email: str = "", password: str = "",
                  whitebox: dict | None = None, project_id: str = "",
                  run_id: str | None = None) -> str:
        run_id = run_id or uuid.uuid4().hex[:10]
        run_dir = config.ARTIFACTS / f"run_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        handle = RunHandle(run_id=run_id)
        handle.run_dir = run_dir
        handle.result = RunResult(run_id=run_id, target_url=target_url,
                                  description=description, project_id=project_id)
        t = threading.Thread(
            target=self._safe_pipeline,
            args=(handle, target_url, description, email, password, whitebox),
            daemon=True)
        handle.thread = t
        self.runs[run_id] = handle
        t.start()
        return run_id

    def run_job(self, run_id: str, payload: dict) -> None:
        """Execute a queued expedition synchronously inside the durable worker."""
        run_dir = config.ARTIFACTS / f"run_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        handle = RunHandle(run_id=run_id, run_dir=run_dir)
        handle.result = RunResult(run_id=run_id, target_url=payload["url"],
                                  description=payload["description"],
                                  project_id=payload.get("project_id", ""))
        self.runs[run_id] = handle
        coordination.save_result(run_id, handle.result.model_dump())
        self._safe_pipeline(handle, payload["url"], payload["description"],
                            payload.get("email", ""), payload.get("password", ""),
                            payload.get("whitebox"))

    @staticmethod
    def _upload_run_artifacts(run_id: str, run_dir: Path) -> None:
        """Copy finished evidence to shared S3-compatible object storage when configured."""
        import os
        bucket = os.getenv("QA_S3_BUCKET")
        if not bucket:
            return
        import boto3
        client = boto3.client(
            "s3", endpoint_url=os.getenv("QA_S3_ENDPOINT_URL") or None,
            region_name=os.getenv("AWS_REGION") or os.getenv("QA_S3_REGION") or None,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"))
        prefix = os.getenv("QA_S3_PREFIX", "qarena").strip("/")
        archive = shutil.make_archive(str(run_dir), "zip", run_dir)
        client.upload_file(archive, bucket, f"{prefix}/run_{run_id}.zip")
        for path in run_dir.rglob("*"):
            if path.is_file():
                key = f"{prefix}/run_{run_id}/{path.relative_to(run_dir).as_posix()}"
                client.upload_file(str(path), bucket, key)

    def emit_of(self, run_id: str):
        h = self.runs[run_id]

        def emit(e: dict) -> None:
            e["ts"] = round(time.time(), 2)
            h.history.append(e)
            h.events.put(e)
            coordination.publish_event(run_id, e)
            try:
                with open(h.run_dir / "run.log", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(e) + "\n")
            except Exception:
                pass
        return emit

    # ------------------------------------------------------------- pipeline
    def _safe_pipeline(self, h: RunHandle, url: str, desc: str,
                       email: str, password: str, whitebox: dict | None):
        try:
            self._pipeline(h, url, desc, email, password, whitebox)
        except Exception as exc:
            h.events.put({"type": "error", "msg": f"{exc.__class__.__name__}: {exc}"})
            if h.result:
                h.result.status = "error"
                h.result.error = str(exc)
        finally:
            self._save(h)
            # Create a report for complete and early-stopped runs. Keep PDF
            # failures isolated so they do not hide the original run result.
            try:
                from .pdf_report import create_pdf_report
                create_pdf_report(h.result, h.run_dir / "qarena_report.pdf")
            except Exception as exc:
                h.events.put({"type": "log", "msg": f"PDF report generation failed: {exc}"})
            if coordination.enabled() and os.getenv("QA_S3_BUCKET"):
                try:
                    self._upload_run_artifacts(h.run_id, h.run_dir)
                except Exception as exc:
                    if h.result:
                        h.result.status = "error"
                        h.result.error = f"Evidence upload failed: {exc}"
                        self._save(h)
            self._save(h)
            done = {"type": "done", "status": h.result.status if h.result else "error",
                    "result": self._result_json(h)}
            h.events.put(done)
            coordination.publish_event(h.run_id, done)

    def _pipeline(self, h: RunHandle, url: str, desc: str,
                  email: str, password: str, whitebox: dict | None):
        llm = LLMPolicy()
        sbx = Sandbox(url, h.run_dir)
        emit = self.emit_of(h.run_id)
        res = h.result
        res.llm_mode = "litellm" if llm.available else "heuristic (key-free)"
        stage_started = time.monotonic()

        def stage(n: int, name: str):
            nonlocal stage_started
            if n >= 3 and res.status == "running":
                res.stage_timings[name] = round(time.monotonic() - stage_started, 2)
            stage_started = time.monotonic()
            emit({"type": "stage", "stage": n, "name": name})

        try:
            # ------------------------------- STAGE 2: sandbox + safety + login
            stage(2, "Sandbox / Safety Guard / Login-once")
            if whitebox and whitebox.get("folder"):
                sbx.boot_local_app(whitebox["folder"], whitebox["command"],
                                   int(whitebox["port"]))
            sbx.wait_until_responsive(url, timeout=20)
            seeded = sbx.seed_target()
            emit({"type": "log", "msg": f"target responsive; seed={'yes' if seeded else 'n/a'}"})
            sbx.start_browser()
            if email:
                ok = sbx.login_once(email, password)
                emit({"type": "log", "msg": f"login-once: {'ok' if ok else 'skipped/failed'}"})
            else:
                if sbx.auth_state_path.exists():
                    sbx.auth_state_path.unlink()

            # ------------------------------- STAGE 3b: exploration
            stage(3, "Explorer — mapping the territory")
            t0 = time.time()
            explorer = Explorer(sbx, h.run_dir, emit)
            appmap = explorer.explore(desc)
            res.states_found = len(appmap.states)
            self._save(h)
            emit({"type": "map", "states": res.states_found,
                  "seconds": round(time.time() - t0, 1)})

            # ------------------------------- STAGE 4: planning + guardrails
            stage(4, "Planner — journeys & adversarial cases")
            planner = Planner(appmap, llm)
            cases, rejected = planner.plan()
            res.tests_planned = len(cases)
            res.rejected_steps = rejected
            emit({"type": "plan", "tests": len(cases), "rejected": len(rejected),
                  "titles": [c.title for c in cases]})
            self._save(h)

            # ------------------------------- STAGE 5a+5b: execute + judge
            tester = Tester(sbx, appmap, h.run_dir, emit)
            judge = Judge(llm)
            verifier = Verifier(tester, judge, emit)
            passed_cases: list[TestCase] = []
            suspects: list[PossibleBug] = []

            for tc in cases:
                stage(5, f"Tester × Judge — {tc.test_id}: {tc.title[:48]}")
                sbx.seed_target()  # per-test world reset when hook exists
                verdict, evidences = tester.run_test(tc, tracing=True)
                res.steps_executed += len(evidences)
                emit({"type": "verdict", "test": tc.test_id, "verdict": verdict})
                test_suspects: list[PossibleBug] = []
                for i, ev in enumerate(evidences):
                    for pb in judge.evaluate(tc.title, tc.test_id, i, ev,
                                             tc.steps[: i + 1]):
                        pb.oracle = [tc.expected.model_dump()]  # replay contract
                        test_suspects.append(pb)
                # cross-step merge: S2 on a dead-link navigation absorbs the
                # S3 'dead link' spotted on the page that hosted it
                merged_here = self._merge_deadlink_pairs(test_suspects)
                suspects.extend(merged_here)
                for pb in merged_here:
                    emit({"type": "suspect", "journey": tc.test_id,
                          "score": pb.score, "summary": pb.summary[:140]})
                if verdict == "passed":
                    passed_cases.append(tc)
                self._save(h)

            # pre-verify dedupe: one verdict per (route, group); content bugs
            # (intent/console families) dedupe globally regardless of route
            unique: dict[tuple, PossibleBug] = {}
            for pb in suspects:
                # content defects (console noise, price/money semantics) fire
                # on every page they appear — dedupe them globally by group so
                # "-$64.95 on /cart, /checkout, /order" is ONE filing
                route_insens = pb.group.startswith(("intent:", "console:",
                                                    "form:money", "flow:badge",
                                                    "axe:"))
                key = ((pb.group,) if route_insens
                       else (self._route_of(pb), pb.group))
                # final-gate contract groups ("silently accepted",
                # "forbidden text") collapse DISTINCT defects that share a
                # route+group label but have different root causes per
                # journey (empty-cart vs empty-address vs bad-qty checkout)
                # — keep one per test instead, else BUG-12-style recall leak
                if pb.group.startswith(("form:invalid input was silently",
                                        "form:forbidden text present")):
                    key = key + (pb.test_id,)
                if key not in unique or pb.score > unique[key].score:
                    unique[key] = pb
            suspects = list(unique.values())
            emit({"type": "log",
                  "msg": f"{len(suspects)} unique suspects after dedupe"})

            # ------------------------------- STAGE 6a: replay verification
            stage(6, "Replay Verifier — proving suspects in clean browsers")
            confirmed: list[tuple[PossibleBug, str]] = []
            for suspect_index, pb in enumerate(suspects):
                if time.time() - t0 > config.RUN_WALL_CLOCK_S:
                    emit({"type": "log", "msg": "run time budget reached; remaining candidates kept in the unproven watchlist"})
                    res.flaky.extend(FlakySuspect(
                        journey=f"{pending.journey} [{pending.group}]", summary=pending.summary[:200],
                        reproduced="not checked", signals=[signal.signal for signal in pending.signals],
                        status="not_checked")
                        for pending in suspects[suspect_index:])
                    break
                ok, count = verifier.verify(pb)
                if ok:
                    confirmed.append((pb, count))
                    emit({"type": "confirmed", "journey": f"{pb.test_id} {pb.group}",
                          "count": count})
                else:
                    res.flaky.append(FlakySuspect(
                        journey=f"{pb.journey} [{pb.group}]", summary=pb.summary[:200],
                        reproduced=count, signals=[s.signal for s in pb.signals]))
                    emit({"type": "flaky", "journey": pb.journey, "count": count})
                self._save(h)

            # ------------------------------- STAGE 7a: dedup + reports
            stage(7, "Reporter — dedup, severity, human-quality reports")
            reporter = Reporter(h.run_dir, llm)
            res.filed_bugs = reporter.file_bugs(confirmed)

            # ------------------------------- STAGE 7b: export + self-verify
            stage(7, "Exporter — pytest suite, self-verified green")
            exporter = Exporter(h.run_dir, url)
            files = exporter.export(passed_cases)
            keep, pytest_log = exporter.self_verify(files) if files else ([], "No passing journeys were eligible for export.\n")
            (h.run_dir / "pytest_self_run.log").write_text(pytest_log, encoding="utf-8")
            res.exported_tests = keep
            res.tests_passed = len(passed_cases)
            res.coverage = self._coverage(appmap, cases, passed_cases)

            # ------------------------------- STAGE 8: benchmark
            stage(8, "Benchmark — precision / recall / F1 vs ground truth")
            from scorer.evaluate_benchmark import evaluate
            res.benchmark = evaluate(h.run_dir)
            res.tokens_used = llm.total_tokens
            res.stage_timings["benchmark"] = round(time.monotonic() - stage_started, 2)
            res.status = "done"
        finally:
            # Preserve usage even when a later report/export stage fails.
            res.tokens_used = llm.total_tokens
            sbx.shutdown()

    # ---------------------------------------------------------------- misc
    @staticmethod
    def _merge_deadlink_pairs(pbs: list[PossibleBug]) -> list[PossibleBug]:
        """If an S3 'dead link' and an S2 '404 document' in the same test point
        at the same URL, they're one defect — merge (S2 suspect absorbs S3)."""
        s3_dead = {}
        for pb in pbs:
            if pb.group.startswith("broken:"):
                for b in pb.evidence.broken_items:
                    if b.get("kind") == "link":
                        s3_dead[(b.get("detail") or "").split(" ")[0]] = pb
        absorbed: set[int] = set()
        for i, pb in enumerate(pbs):
            if not pb.group.startswith("http:"):
                continue
            path = pb.group[5:]
            for dead_url, s3pb in list(s3_dead.items()):
                if dead_url and path.endswith(dead_url):
                    sig = {h.signal for h in pb.signals}
                    if 3 not in sig:
                        pb.signals = s3pb.signals + pb.signals
                        pb.score = min(1.0, round(pb.score + 0.3, 3))
                        pb.summary += " | " + s3pb.summary
                    absorbed.add(id(s3pb))
        return [pb for pb in pbs if id(pb) not in absorbed]

    @staticmethod
    def _route_of(pb: PossibleBug) -> str:
        import re
        from urllib.parse import urlparse
        url = pb.evidence.url_after or pb.evidence.url_before or ""
        return (re.sub(r"/\d+(?=/|$)", "/*", urlparse(url).path) or "/").rstrip("/") or "/"

    @staticmethod
    def _coverage(appmap, cases, passed) -> dict:
        routes_hit = set()
        for tc in cases:
            for st in tc.steps:
                if st.action == "goto":
                    routes_hit.add(st.target)
        total_routes = len({s.route for s in appmap.states if s.http_status == 200})
        elements_total = sum(len(s.elements) for s in appmap.states
                             if s.http_status == 200)
        exercised = set()
        for tc in cases:
            for st in tc.steps:
                if st.role:
                    exercised.add((st.role, st.name))
        return {
            "route_coverage": round(min(1.0, len(routes_hit) / max(total_routes, 1)) * 100, 1),
            "routes_tested": len(routes_hit), "routes_total": total_routes,
            "element_coverage": round(min(1.0, len(exercised) / max(elements_total, 1)) * 100, 1),
            "elements_exercised": len(exercised), "elements_total": elements_total,
        }

    @staticmethod
    def _result_json(h: RunHandle) -> dict:
        return h.result.model_dump() if h.result else {}

    def _save(self, h: RunHandle) -> None:
        if h.result and h.run_dir:
            destination = h.run_dir / "results.json"
            temporary = h.run_dir / "results.json.tmp"
            temporary.write_text(h.result.model_dump_json(indent=2), encoding="utf-8")
            os.replace(temporary, destination)
            coordination.save_result(h.run_id, h.result.model_dump())

    # ------------------------------------------------------------- utilities
    def latest_run(self) -> RunHandle | None:
        return self.runs[sorted(self.runs)[-1]] if self.runs else None

    def zip_run(self, run_id: str) -> Path | None:
        h = self.runs.get(run_id)
        if not h:
            return None
        archive = shutil.make_archive(str(h.run_dir), "zip", h.run_dir)
        return Path(archive)
