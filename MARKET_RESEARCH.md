# QArena — Market Research & Competitive Analysis
**Problem IM-02: Autonomous AI QA Engineer · CODE APEX 3.0 (iMocha Track) · Vishwakarma Institute of Technology, Pune**
*Prepared: 11 Oct 2026 — analysis only, no build started*

---

## 1. What the problem statement is REALLY testing (read between the lines)

The IM-02 brief is **evidence-driven evaluation**, not demo-driven. The three expected outcomes are all measurable:

1. **Planted-bug recall** — "finds and correctly reports a *measurable share*" of deliberately planted bugs → they will compare your reports against a ground-truth list.
2. **Test runnability** — "generated tests run on their own and can be repeated" → they will re-run your exported tests.
3. **Human reproducibility** — "every bug report can be reproduced by a person following its steps" → they will hand your report to a human and time them.

This means the winning design optimizes **precision × reproducibility**, not "cool agent walks the app". A single hallucinated bug report costs more than a missed bug.

**Stage gates (do not overbuild early):**
| Hackathon stage | Must demo |
|---|---|
| Stage 1 — Idea & presentation | Problem framing, agent design (sees/decides/does), target sample app, accuracy+coverage measurement plan |
| Stage 2 — Online prototype | ONE key journey (sign-up/login) tested end-to-end, ≥1 exported test, ≥1 evidence-backed bug report |
| Stage 3 — 24h grand finale | Multi-journey exploration, cooperating agents, planted-bug benchmark results, findings dashboard |

---

## 2. Market size & timing

- Automation-testing market: **$20.6–37.4B in 2025**, growing at **~15–17% CAGR** to $84–155B by 2034/35. AI is explicitly named as the growth driver. (Figures vary by analyst; use a conservative $20B+ and ~17% CAGR in the pitch.)
- Asia-Pacific is the largest/fastest region — relevant for an India-built, small-team-first tool.
- **2025–2026 is the "agent" inflection**: virtually every major QA vendor shipped an agentic product within the last 12 months → the category is validated, but the small-team/evidence niche is unclaimed.

## 3. QA pain statistics (ammunition for the pitch)

| Stat | Source context |
|---|---|
| 15–25% of E2E tests are flaky; Google found ~16% of its tests flaky | Google Engineering, Diffie State of E2E 2026 |
| Mean time to fix ONE flaky test: **3.7 engineering hours** | Google (2020) |
| **30–40% of total QA effort goes to maintaining** existing tests | Capgemini World Quality Report 2024-25 |
| 49% of teams cite test maintenance as their #1 testing challenge | Capgemini WQR |
| 55% of teams using open-source frameworks spend **>20 hrs/week** creating+maintaining tests | Rainforest QA 2024 survey (n=625) |
| Only **4% of IT leaders** say AI testing features worked "Very Well"; ~41% abandon these tools within a year | Pulse Opinion Research / industry reports |
| Selector breakage is only **~28% of real failure causes** — timing ~30%, data ~14%, visual ~10% | QA Wolf failure breakdown |
| Self-healing implementations measured with **23% higher false-positive rates**; 60% of teams disable the AI features within 3 months; tests run 2–3× slower | Ranorex enterprise analysis |
| Selector failures per run create CI noise: 400 tests @ 5% flake ≈ 20 false failures/run | getautonoma blog synthesis |

**Narrative this builds:** everyone sells *self-healing* and *pass/fail dashboards*; the industry is quietly drowning in false positives and maintenance. QArena's "never heal, verify 3×, export only tests that pass on their own" stance is the counter-position.

## 4. Competitor landscape (as of Oct 2026)

### 4.1 Enterprise incumbents shipped agents — but at enterprise prices with enterprise lock-in
| Vendor | Agent product | Shipped | Weakness for our niche |
|---|---|---|---|
| Sauce Labs | AURA | Jul 2026 | Execution infrastructure + analytics; AI agents new/evolving; expensive |
| mabl | Agentic Tester / Active Coverage | Apr 2026 | Scripted execution under the hood, credit pricing unpredictable, still selector-aware |
| Tricentis | AI Workspace (4 agents) | Mar 2026 | SAP/Oracle estates, quote-only, vendor-locked model-based format |
| Autify | Aximo | pre-2026 | Black-box SaaS, enterprise pricing not public, agent needs review on critical paths |
| TestMu (LambdaTest) | KaneAI | pre-2026 | Tied to their cloud grid; audit trail moderate; tests export exists but platform-bound |
| Functionize | Studio agents | 2025-26 | Enterprise SaaS, heavy |
| Momentic | AI-native platform | YC-backed, Series A | Proprietary, cloud-only, **~$500–1,000+/mo**; NL-prompt driven |
| ACCELQ | Autopilot | — | Tests live in proprietary object model, no documented export path |

### 4.2 Open-source / AI-native comparators (the real bar)
| Tool | License | What it does | Gap QArena exploits |
|---|---|---|---|
| **Playwright Test Agents** (official Microsoft, v1.56, Oct 2025) + Playwright MCP | Apache/MIT | Planner explores app via aria tree & writes Markdown plans; Generator writes specs; Healer fixes locators | They **author tests, they don't judge bugs**. No evidence bundle, no 3× replay verification, no dedup, no severity, no bug reports, no planted-bug benchmark, no dashboard. Healer = the false-positive anti-pattern. **Judges WILL ask about this — memorize the diff.** |
| Explorbot (Testomat.io) | Open source | Explores app, tries unexpected/malicious inputs, saves Playwright/CodeceptJS tests; 30–50 tests/hr; added "Jev" for deterministic decisions | Closest OSS comparator. No deterministic replay-verification gate, no weighted multi-signal judging, no white-box diff mapping, no pytest self-verifying export, benchmark story absent |
| Skyvern | AGPL-3.0, 12k+ stars | Vision+LLM browser workflow agent (RPA-grade) | General-purpose automation, not a QA judge; no evidence/reproducibility pipeline |
| Autonoma | BSL 1.1 → Apache 2028 | 4-agent pipeline (Planner/Executor/Reviewer/Diffs) generating tests from codebase | Test-generation focus, not bug-report-first; BSL not fully OSS; no human-reproducibility guarantee |
| Stagehand / Shortest / Midscene | MIT | LLM step resolvers around Playwright | Scripting helpers, not autonomous QA |

### 4.3 Hackathon intel
A public repo `Bliss37/autonomous-ai-qa-hackathon` (commit 8 Oct 2026) targets the same IM-02 with the obvious explorer/tester/reporter multi-agent narrative. **Conclusion: "three cooperating agents" alone will NOT differentiate us** — it is the baseline every team will pitch. Our differentiators must be the ones nobody else has:
1. **pass³ Deterministic Replay Verifier** (zero-AI, 2-of-3 reproduction gate) → provable zero-hallucination reports
2. **6-Signal Weighted Bug Judge** with LLM limited to exactly 4 calls total
3. **Self-verifying pytest export** (exported tests are RUN immediately; failures dropped → 100% delivered-green guarantee)
4. **Stage 8 benchmark scorer** (Precision/Recall/F1/Reproducibility vs bugs.json, averaged over 3–5 runs) — an honest number incumbents never publish
5. **Zero lock-in**: plain pytest-playwright files + Markdown reports in the user's repo

## 5. Technical research — what actually breaks browser agents (and our counters)

Sourced from WebArena/AgentOccam/related research:
- GPT-4-class agents scored **~14% on WebArena**; SOTA (IBM CUGA) ~**61.7%**; humans ~78%. Harder suites (WebChoreArena) drop top models to ~37.8%.
- Documented failure modes: **hallucinated page content when stuck, invalid action loops, excessive scroll-looping, popup/CAPTCHA confusion, memory overflow on long horizons**.
- **Implication for QArena:** an LLM-per-step design will visibly fail in a live demo and burn tokens. The 3-tier architecture (Playwright deterministic layer / Python control layer / LiteLLM restricted to exactly 4 calls) is not a cost optimization — it is **demo survival and scoring survival**. This is the "hard truth" most competing teams will learn the hard way on stage.
- Safety: real web pages can contain prompt-injection text → page content fed to LLM #1/#2 must be sanitized/delimited; agent needs the Stage 2 Safety Guard (domain allowlist, destructive-click blocklist, locator_handler for cookie modals, hard step/time/page budgets).

## 6. Build-risk register (the genuinely hard engineering, ranked)

1. **State-hash canonicalization (Stage 3b)** — `SHA256(ARIA snapshot)` must ignore volatile DOM junk (timestamps, CSRF tokes, random ids, ad noise) or the App Map either explodes or merges distinct states. Fix: strip volatile attributes + URL-pattern normalization rules.
2. **Invented-step rejection vs fuzzy naming (Stage 4)** — LLM writes `Pay`, AppMap has `Pay Now`. Too strict → reject valid plans; too loose → admit hallucinations. Needs normalized-substring + role matching policy with logged rejections.
3. **Signal 4 on negative tests (Stage 5b)** — negative cases are *supposed* to trigger validation errors; distinguishing "expected rejection" from "improperly accepted" needs the Pydantic `expected` contract enforced deterministically, not by vibes.
4. **Signal 6 intent check** — the riskiest false-positive source; must be constrained to the app's stated description, low weight, quote-evidence required.
5. **2-of-3 replay gate vs genuinely intermittent bugs (Stage 6a)** — a real race condition reproduces ~1/3 and gets flake-flagged, never filed. Acceptable (precision-first) but should surface as a "flaky-suspect" dashboard card, with optional 5× rerun to catch borderline cases.
6. **Deterministic locators in export (Stage 7b)** — use `get_by_role` resilient chain + `storage_state` + explicit baseURL; placeholders in Jinja2 must survive paths/ports differences.
7. **Planted-bug design (sample_app)** — bugs must be (a) only reachable via adversarial behavior, (b) **deterministically reproducible**, (c) one bug per signal (console / HTTP 500 / broken asset / negative-form accepted / axe violation / intent mismatch). A race-condition bug would sabotage our own reproducibility metric — don't plant one for the scored run.
8. **Determinism at demo** — sample app needs a `/__seed__` reset endpoint so every judge run starts from identical DB state.
9. **LLM failure modes at venue** — no guarantee of venue internet/API quota → LiteLLM provider failover chain + **cached LLM response mode** (recorded call fixtures) so the 4 calls never block a demo.
10. **Token/latency budget** — dashboard must show total token cost per run; target the full demo run in single-digit minutes within a token cap.

## 7. Attack plan for build order (when you say go)

**Build order (risk-first):**
1. `sample_app/` + `bugs.json` + seed endpoint (the benchmark is the product — everything else is scored against it)
2. `core/sandbox.py` Storage-state login + safety guard
3. `core/explorer.py` AppMap with canonical hashing + pyvis map
4. `core/planner.py` LLM #1 with Pydantic validation + AppMap rejection
5. `core/tester.py` + `core/judge.py` 6 signals + weighted scorer
6. `core/verifier.py` 3× replay gate (the crown jewel)
7. `core/reporter.py` dedup + Jinja2 reports + pytest export + immediate pytest self-run
8. `app.py` Streamlit input form + dashboard (pyvis map, bug cards, coverage, token meter)
9. `scorer/evaluate_benchmark.py` P/R/F1/reproducibility, 3–5-run batch average
10. Stage 3a + 6b white-box (GitPython/tree-sitter/ripgrep) LAST — demo-black-box must work alone.
