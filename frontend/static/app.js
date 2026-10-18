/* QArena live expedition view — SSE-driven, no frameworks.
   Design rule: every line must make sense to a first-time user. */
(function () {
  const runId = location.pathname.split("/").pop();
  const $ = (id) => document.getElementById(id);
  const state = {
    suspects: [], flaky: [], rejected: [], result: null,
    steps: 0, done: false,
  };
  let fullResult = null;
  const t0 = Date.now();
  const clockTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    $("clock").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 1000);

  // ------------------------------------------------ translator: raw → plain English
  const SIG_NAMES = { 1: "console errors", 2: "broken requests", 3: "broken page content",
                      4: "form validation", 5: "accessibility", 6: "business-rule break" };

  function humanSource(str) {
    // suspect groups/summaries → a sentence a user understands
    let s = String(str || "");
    let m;
    if ((m = s.match(/^TC_\d+\s+(.*)$/))) s = m[1];                  // strip journey prefix
    if ((m = s.match(/^S(\d)\s+([^:]+):\s*(.*)$/))) s = m[3];        // strip "S5 Accessibility:"
    s = s.split(" | ")[0].trim();                                    // keep the primary signal only
    if ((m = s.match(/^GET\s+(https?:\S+)\s*→\s*(\d+)/))) return `broken page load — ${m[1]} answers ${m[2]}`;
    if ((m = s.match(/^POST\s+(https?:\S+)\s*→\s*(\d+)/))) return `broken form submit — ${m[1]} answers ${m[2]}`;
    if ((m = s.match(/^http:(\S+)/))) return `broken request to ${m[1]}`;
    if ((m = s.match(/^console:(.*)/))) return `JavaScript error: ${m[1].trim()}`;
    if ((m = s.match(/^axe:(\S+)\s*(.*)/)))
      return `accessibility violation (${m[1].replace("a11y-", "").replace(/-/g, " ")})${m[2] ? " — " + m[2] : ""}`;
    if (s.startsWith("form:money")) return "money amount went negative on the page";
    if (s.startsWith("form:invalid input was silently"))
      return "invalid input was accepted with no error message";
    if ((m = s.match(/^form:forbidden text present:\s*(.+)/))) return `page shows text it never should: ${m[1]}`;
    if ((m = s.match(/^form:reached disallowed URL\s*(.+)/))) return `user reached a page the app should have blocked (${m[1]})`;
    if ((m = s.match(/^intent:(.*)/))) return `business-rule break — ${m[1].trim()}`;
    if ((m = s.match(/^flow:badge (.*)/))) return `cart badge is wrong — ${m[1].trim()}`;
    if ((m = s.match(/^expect:(.*)/))) return m[1].trim();
    return s.replace(/[|]/g, "·").trim().slice(0, 160);
  }
  function journeyOf(str) {
    const m = String(str || "").match(/^(TC_\d+)/);
    return m ? m[1] : "";
  }

  // ---------------------------------------------------------------- helpers
  function log(msg, cls) {
    const li = document.createElement("li");
    if (cls) li.className = cls;
    li.textContent = msg;
    $("log").appendChild(li);
    $("log").scrollTop = $("log").scrollHeight;
  }
  function setStage(n, name) {
    document.querySelectorAll("#stepper li").forEach((li) => {
      const s = +li.dataset.stage;
      li.classList.toggle("done", s < n);
      li.classList.toggle("active", s === n);
    });
    log(`▶ ${name}`, "stage-ev");
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // ---------------------------------------------------------------- events
  function handle(ev) {
    switch (ev.type) {
      case "stage":
        setStage(ev.stage, ev.name);
        break;
      case "log": {
        let msg = ev.msg;
        const dm = String(msg).match(/^(\d+) unique suspects after dedupe$/);
        if (dm) msg = `${dm[1]} unique possible bugs to double-check (duplicates removed)`;
        else if (msg === "target responsive; seed=yes") msg = "Target answered — it's reachable. Crawling is safe to begin";
        else if (msg === "login-once: ok") msg = "Signed in once with the test credentials";
        log("· " + msg); break;
      }
      case "state":
        bump("st-states");
        log(`Found page ${ev.route} [${ev.status}] — ${ev.elements} interactive controls`);
        break;
      case "map":
        log(`Site map complete: ${ev.states} pages discovered in ${ev.seconds}s`, "ok");
        $("map-card").hidden = false;
        $("map-frame").src = `/artifacts/${runId}/map.html`;
        break;
      case "plan":
        $("st-tests").textContent = ev.tests;
        log(`Test plan ready: ${ev.tests} journeys. (${ev.rejected} ungrounded ideas auto-rejected at the guardrail)`, "ok");
        break;
      case "suspect": {
        state.suspects.push(ev);
        $("st-suspects").textContent = state.suspects.length;
        $("suspect-tray").hidden = false;
        const what = humanSource(ev.summary);
        const where = journeyOf(ev.journey) ? `spotted during journey ${journeyOf(ev.journey)}` : "spotted while exploring";
        renderTray("suspect-list", state.suspects,
          (s) => `<b>${esc(humanSource(s.summary))}</b><br>
                  <span class="mini">${esc(journeyOf(s.journey) ? "during journey " + journeyOf(s.journey) : "while exploring")} · confidence ${s.score} · being double-checked…</span>`);
        log(`⚑ Possible bug: ${what} (${where}, confidence ${ev.score}) — will prove it before reporting`, "bug");
        break;
      }
      case "confirmed":
        state.confirmed = (state.confirmed || 0) + 1;
        $("st-confirmed").textContent = state.confirmed;
        log(`✓ PROVEN bug: ${humanSource(ev.journey)} — reproduced ${ev.count} in fresh browsers`, "bug");
        break;
      case "flaky":
        state.flaky.push(ev);
        $("st-flaky").textContent = state.flaky.length;
        $("flaky-tray").hidden = false;
        renderTray("flaky-list", state.flaky,
          (s) => `<b>${esc(humanSource(s.summary))}</b> — ${s.count ? `reappeared only ${esc(s.count)} of 3 tries` : "not checked before the run time limit"}`);
        log(ev.count ? `✖ Dropped: ${humanSource(ev.journey)} — appeared only ${ev.count} of 3 replays, not solid enough to report` : `◷ Not verified before run budget: ${humanSource(ev.journey)}`, "");
        break;
      case "step":
        state.steps += 1;
        $("st-steps").textContent = state.steps;
        break;
      case "replay":
        log(`  double-check ${ev.attempt}/3 on “${humanSource(ev.journey)}” — ${ev.matched ? "bug happened again ✓" : "did not reappear"}`);
        break;
      case "verdict":
        log(ev.verdict === "passed"
          ? `Journey ${ev.test}: behaved exactly as the description says ✓`
          : `Journey ${ev.test}: broke a promised rule — looking for solid proof…`,
          ev.verdict === "passed" ? "ok" : "");
        break;
      case "error":
        log(`STOPPED: ${ev.msg}`, "bug");
        break;
      case "done":
        onDone(ev.result);
        es.close();
        break;
    }
  }
  function bump(id) { $(id).textContent = (+$(id).textContent || 0) + 1; }
  function renderTray(listId, items, tpl) {
    $(listId).innerHTML = items.map((i) => `<li>${tpl(i)}</li>`).join("");
  }

  // ---------------------------------------------------------------- done
  async function onDone(result) {
    if (state.done) return;
    state.done = true;
    clearInterval(clockTimer);
    document.querySelectorAll("#stepper li").forEach((li) => {
      li.classList.remove("active");
      li.classList.add("done");
    });
    const pill = $("status-pill");
    const blocked = result && result.status === "blocked";
    const failed = result && result.status === "error";
    pill.textContent = blocked ? "ACCESS BLOCKED" : failed ? "STOPPED EARLY" : "COMPLETE";
    pill.className = "pill mono " + (blocked || failed ? "error" : "done");
    $("zip-btn").hidden = false;
    $("pdf-btn").hidden = false;
    $("findings").hidden = false;
    let res = result || {};
    try {
      const latest = await fetch(`/api/runs/${runId}/result`);
      if (latest.ok) res = await latest.json();
    } catch (_) { /* use the final SSE payload if the follow-up fetch is unavailable */ }
    fullResult = res;
    // pin KPI tiles to results.json — history replay is a retelling, not accounting
    $("st-confirmed").textContent = (res.filed_bugs || []).length;
    if (res.steps_executed != null) $("st-steps").textContent = res.steps_executed;
    if (res.states_found != null) $("st-states").textContent = res.states_found;
    if (res.tests_planned != null) $("st-tests").textContent = res.tests_planned;
    renderFindings(res);
    if (res.project_id) $("team-tools").hidden = false;
  }

  function renderFindings(res) {
    $("tokens").textContent = res.tokens_used || 0;
    const aiMode = String(res.llm_mode || "heuristic (key-free)");
    const aiWasOff = /heuristic|key-free|offline/i.test(aiMode);
    log(aiWasOff
      ? `AI was not enabled (${aiMode}); 0 tokens is expected. Add a provider API key to .env to enable AI-assisted link ranking and test planning.`
      : `AI mode: ${aiMode}; provider-reported usage: ${res.tokens_used || 0} tokens.`);
    log(`expedition ${res.status === "error" ? "stopped early" : "complete"}`);

    if (res.status === "error" && res.error) {
      $("error-card").hidden = false;
      $("error-msg").textContent = res.error;
    }
    if (res.benchmark && res.benchmark.available) {
      const b = res.benchmark;
      $("bench-card").hidden = false;
      $("bench-grid").innerHTML = [
        [b.precision, "precision — of the bugs we filed, how many were real"],
        [b.recall, "recall — of the planted bugs, how many we found"],
        [b.f1, "F1 — combined score"],
        [`${b.reproducibility_pct}%`, "of reports reproducible"],
        [`${b.true_positives}/${b.planted_bugs}`, "planted bugs found"],
      ].map(([v, l]) => `<div class="bstat" title="${esc(l)}"><b>${v}</b><span>${esc(l.split("—")[0].trim())}</span></div>`).join("");
      $("bench-missed").textContent = b.missed && b.missed.length
        ? `honest admission — planted bugs we did not find: ${b.missed.join(", ")}`
        : "no planted bug escaped — every one found and proven 🎉";
    }
    if (res.coverage && res.coverage.routes_total) {
      $("cov-row").textContent =
        `Coverage: ${res.coverage.route_coverage}% of pages tested (${res.coverage.routes_tested}/${res.coverage.routes_total}) · ` +
        `${res.coverage.element_coverage}% of controls exercised (${res.coverage.elements_exercised}/${res.coverage.elements_total}) · ` +
        `${(res.exported_tests || []).length} reusable regression tests exported`;
    }
    const timings = res.stage_timings || {};
    const slowest = Object.entries(timings).sort((a, b) => b[1] - a[1])[0];
    $("run-advice").hidden = false;
    $("run-advice-text").textContent = slowest
      ? `The longest measured QArena stage was ${slowest[0]} (${Number(slowest[1]).toFixed(1)} seconds). Optimize that measured stage first. This run does not inspect enough target source code to justify a React, Next.js, or Python migration; fix findings in the current stack and profile before changing architecture.`
      : `No stage timings were recorded, so a performance bottleneck cannot be identified from this run. Browser findings alone do not justify changing the target's framework; use repository inspection and profiling first.`;
    const bugs = res.filed_bugs || [];
    if (bugs.length) {
      $("specimens").insertAdjacentHTML("beforebegin",
        `<div class="sev-legend mono">reading the stamps:
         <b class="sev Critical">CRITICAL</b> breaks a core flow ·
         <b class="sev Major">MAJOR</b> blocks or misleads users ·
         <b class="sev Minor">MINOR</b> fix when convenient</div>`);
    }
    const hadNoTestCoverage = !res.states_found && !res.tests_planned && !res.steps_executed;
    $("specimens").innerHTML = bugs.length
      ? bugs.map(specCard).join("")
      : res.access_blocked_reason
        ? `<div class="card error-card"><h3>Access blocked — result inconclusive</h3>
           <p class="explain">${esc(res.access_blocked_reason)} No bugs were filed because QArena did not reach the application.</p></div>`
      : res.status === "error" || hadNoTestCoverage
        ? `<div class="card error-card"><h3>Bug status unknown</h3>
           <p class="explain">QArena did not complete enough testing to determine whether this website has bugs. Zero confirmed bugs here does not mean the website is bug-free. Resolve the run error and try again.</p></div>`
        : `<div class="card clean-card"><h3>No proven bugs 🎉</h3>
           <p class="explain">QArena completed this run but did not confirm a bug in the journeys it tested. This does not prove the entire website is bug-free; results are limited to the pages and behaviors reached in this run.</p></div>`;
    if (res.exported_tests && res.exported_tests.length) {
      $("tests-card").hidden = false;
      $("tests-list").innerHTML = res.exported_tests
        .map((f) => `<li><span>${esc(f)}</span>
           <a class="chip" href="/artifacts/${runId}/exported_tests/${esc(f)}">view</a></li>`)
        .join("") + `<li><span>proof they pass — pytest self-run log</span>
           <a class="chip" href="/artifacts/${runId}/pytest_self_run.log">view</a></li>`;
    }
    if (res.rejected_steps && res.rejected_steps.length) {
      $("rejected-tray").hidden = false;
      $("rejected-list").innerHTML = res.rejected_steps
        .map((r) => `<li><b>${esc(r.test_id)}</b> ${esc(r.title)} — refused: ${(r.invented_steps || []).map(esc).join(", ")}</li>`)
        .join("");
    }
  }

  const roleLoad = $("role-load");
  const roleDownload = $("role-download");
  const reportRole = $("report-role");
  const roleStatus = $("role-status");
  if (reportRole && roleDownload) reportRole.addEventListener("change", () => {
    roleDownload.href = `/api/runs/${runId}/report/${reportRole.value}/download`;
    if (roleStatus) roleStatus.textContent = "";
  });
  if (roleLoad) roleLoad.addEventListener("click", async () => {
    const role = $("report-role").value;
    roleLoad.disabled = true;
    if (roleStatus) roleStatus.textContent = "Loading report view…";
    try {
      const response = await fetch(`/api/runs/${runId}/report/${role}`);
      const view = await response.json();
      if (!response.ok) throw new Error(view.detail || "The report view could not be loaded.");
      if (!fullResult) {
        const fullResponse = await fetch(`/api/runs/${runId}/result`);
        const latest = await fullResponse.json();
        if (!fullResponse.ok) throw new Error(latest.detail || "The run result could not be loaded.");
        fullResult = latest;
      }
      renderFindings({...fullResult, filed_bugs: view.bugs});
      if (roleStatus) roleStatus.textContent = `${view.role_name}: showing ${view.bugs.length} finding${view.bugs.length === 1 ? "" : "s"}.`;
      $("specimens").scrollIntoView({behavior: "smooth", block: "start"});
    } catch (error) {
      if (roleStatus) roleStatus.textContent = error.message || "The report view could not be loaded.";
    } finally {
      roleLoad.disabled = false;
    }
  });
  const shareBtn = $("share-report");
  if (shareBtn) shareBtn.addEventListener("click", async () => {
    const response = await fetch(`/api/runs/${runId}/share`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({role: $("report-role").value})});
    const data = await response.json();
    const shareUrl = new URL(data.url, location.origin).href;
    try { await navigator.clipboard.writeText(shareUrl); $("share-result").textContent = `Share link copied: ${shareUrl}`; }
    catch (_) { $("share-result").innerHTML = `Share link: <a href="${esc(shareUrl)}">${esc(shareUrl)}</a>`; }
  });

  // turn machine titles into one sentence a non-engineer understands
  function headlineFor(b) {
    const t = `${b.title} ${b.actual_behavior}`.toLowerCase();
    const page = b.route && b.route !== "/" ? `the ${b.route} page` : "the page";
    if (/404/.test(t) && (b.signals || []).includes(2))
      return `Dead route — ${page} doesn't exist (404 error)`;
    if (/500/.test(t) && (b.signals || []).includes(2))
      return `Server crash — this action returns HTTP 500`;
    if (/price mismatch/.test(t)) return `Wrong price shown — doesn't match the catalog`;
    if (/badge expected/.test(t)) return `Cart counter shows the wrong number`;
    if (/negative money|money went negative|-\$/.test(t)) return `Money allowed below zero — totals can go negative`;
    if (/silently accepted|no validation message/.test(t)) return `Bad input accepted silently — no error message shown`;
    if (/disallowed url/.test(t)) return `Users can reach a page the app must block`;
    if (/#\$0\.00 order confirmed|\$0\.00 order/.test(t)) return `Checkout pays an empty cart — $0.00 “Order confirmed”`;
    if (/order confirmed/.test(t) && b.signals.includes(4)) return `Order goes through despite missing/invalid details`;
    if (/color-contrast|colour-contrast/.test(t)) return `Text is hard to read — fails color-contrast accessibility rule`;
    if (/placeholder-only-label/.test(t)) return `Form field has no real label — screen-reader users get lost`;
    if (/a11y|axe|accessib/.test(t)) return `Accessibility violation found by axe`;
    if (/forbidden text present/.test(t)) return `Page shows an error message instead of working`;
    if (/is not defined|uncaught|console/.test(t)) return `JavaScript error breaks part of the page`;
    if (/forbidden|expect:/.test(t)) return `Page shows the wrong content for this action`;
    return b.title;
  }

  function specCard(b) {
    const chips = (b.signals || [])
      .map((s) => `<span class="chip">${esc(SIG_NAMES[s] || `signal ${s}`)}</span>`).join("");
    const steps = (b.steps_to_reproduce || []).slice(0, 6)
      .map((s) => `<li>${esc(s.replace(/^\d+\.\s*/, ""))}</li>`).join("");
    const shot = b.evidence.screenshot
      ? `<img src="/artifacts/${runId}/${esc(b.evidence.screenshot)}" alt="screenshot at the moment the bug happened">` : "";
    const where = b.route ? `page ${b.route}` : "on this site";
    // evidence strings carry signal tags — present them in plain words
    const untag = (s) => (s || "").replace(/S\d+ [A-Za-z ]+:/g, "").replace(/\s*\|\s*/g, " — ").trim();
    const wrong = untag(b.actual_behavior).slice(0, 240);
    const wanted = (b.expected_behavior || "").trim().slice(0, 240);
    const rec = b.recommendations;
    const suggestions = rec ? `<details class="qa recommendations"><summary>Likely cause &amp; fix suggestions</summary>
      <p><b>Likely cause:</b> ${esc(rec.likely_cause)}</p>
      <ul>${(rec.recommended_fix || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <p><b>Stack advice:</b> ${esc(rec.stack_advice)}</p>
      <p class="micro">${esc(rec.confidence_note)}</p>
      <p class="micro"><b>Publicly documented competitor approaches:</b> ${(rec.competitor_methods || []).map((x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.name)}</a> — ${esc(x.method)}`).join(" · ")}</p></details>` : "";
    return `<article class="spec">
      <span class="sev ${esc(b.severity)}">${esc(b.severity)}</span>
      <span class="sid">${esc(b.bug_id)}</span>
      <h3>${esc(headlineFor(b))}</h3>
      <div class="tech-title mono">${esc(b.title)}</div>
      <div class="loc">📍 Found on <b>${esc(where)}</b>, while testing “${esc(b.journey)}”</div>
      ${wrong ? `<div class="qa"><b>What goes wrong:</b> ${esc(wrong)}</div>` : ""}
      ${wanted ? `<div class="qa"><b>What should happen:</b> ${esc(wanted)}</div>` : ""}
      <div class="chipset">${chips}</div>
      <p class="repro">✔ proof: reproduced ${esc(b.reproduced_count)} in fresh browsers</p>
      ${steps ? `<p class="mini"><b>Try it yourself:</b></p><ol>${steps}</ol>` : ""}
      ${suggestions}
      ${shot}
      <div class="actions">
        <a href="/artifacts/${runId}/bug_reports/${esc(b.bug_id)}.md">full report (.md)</a>
        ${b.evidence.screenshot ? `<a href="/artifacts/${runId}/${esc(b.evidence.screenshot)}">screenshot</a>` : ""}
        <a href="/artifacts/${runId}/${esc(b.evidence.trace)}">browser trace (.zip)</a>
      </div>
    </article>`;
  }

  // ------------------------------------------------------------- bootstrap
  const es = new EventSource(`/api/runs/${runId}/events`);
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "snapshot") {
      if (state.done) { es.close(); return; }   // EventSource auto-reconnects
      ev.history.forEach(handle);               // — replay must stay idempotent
      if (ev.result && ["done", "error", "blocked"].includes(ev.result.status)) {
        onDone(ev.result);
        es.close();                             // a finished run never streams again
      }
      return;
    }
    if (ev.type === "ping") return;
    handle(ev);
  };
  es.onerror = async () => {
    if (state.done) return;
    try {
      const response = await fetch(`/api/runs/${runId}/result`);
      if (response.status === 410) {
        const unavailable = await response.json();
        clearInterval(clockTimer);
        es.close();
        const pill = $("status-pill");
        pill.textContent = "RUN LOST";
        pill.className = "pill mono error";
        $("log").replaceChildren();
        log(unavailable.message || "This run can’t reconnect to its browser worker. Start a new run on the persistent deployment.", "bug");
        return;
      }
    } catch (_) { /* keep the reconnect path for temporary network drops */ }
    log("…reconnecting to the field unit");
  };
})();
