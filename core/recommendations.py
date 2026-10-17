"""Evidence-led remediation and publicly documented competitor approaches."""
from __future__ import annotations


PUBLIC_METHODS = {
    "playwright": ("Playwright Trace Viewer", "Review the exact action, DOM snapshot, console and network activity around the failure.",
                   "https://playwright.dev/docs/trace-viewer"),
    "browserstack": ("BrowserStack Accessibility Testing", "Run accessibility scans and review rule-level issues and remediation guidance.",
                     "https://www.browserstack.com/docs/accessibility/test-types"),
    "applitools": ("Applitools Eyes", "Compare visual checkpoints and inspect DOM/CSS root-cause data for visual differences.",
                   "https://applitools.com/docs/eyes/concepts/reviewing-tests/test-%26-step-editor"),
}


def advise(bug) -> dict:
    """Make cautious recommendations from the bug's recorded signals only."""
    signals = set(bug.signals or [])
    evidence = " ".join(str(x) for x in (bug.evidence or {}).get("signals", []))
    text = f"{bug.title} {bug.actual_behavior} {evidence}".lower()
    cause = "The evidence confirms this behavior is reproducible, but browser evidence alone cannot identify the exact source-code line. Inspect the attached trace and logs before assigning a code-level root cause."
    fixes = ["Open the attached Playwright trace at the failing step.",
            "Make the smallest change that addresses the observed behavior, then rerun this journey and the full regression suite."]
    comps = ["playwright"]
    if 5 in signals or "axe:" in text or "contrast" in text or "label" in text:
        if "contrast" in text:
            cause = "The accessibility scanner observed text with insufficient contrast. The evidence does not establish which stylesheet rule introduced it."
            fixes = ["Inspect foreground/background colors for the reported element and calculate the contrast ratio against the applicable WCAG criterion.",
                     "Adjust the design token or component style, then rerun the accessibility scan at the same viewport."]
        elif "placeholder" in text or "label" in text:
            cause = "The form control appears to lack a persistent accessible name; placeholder text alone may not provide a durable label."
            fixes = ["Add a visible <label for=...> associated with the input, or provide an appropriate accessible name when a visible label is not possible.",
                     "Verify the computed accessible name and test with a screen reader."]
        comps.append("browserstack")
    if 2 in signals or "http:" in text or "404" in text or "500" in text:
        cause = "A reproducible HTTP/network failure was observed. The browser evidence identifies the failing route/response, but server logs are needed to confirm the backend root cause."
        fixes = ["Check the exact request URL, method, response status, and server log entry for this timestamp.",
                 "Correct the route/handler or response contract; add a regression test for this request and its expected status/body."]
    if 1 in signals or "console" in text or "uncaught" in text:
        cause = "A JavaScript console error was recorded. Inspect the first stack frame in the trace to locate the component or script that threw."
        fixes = ["Use the console stack trace and source map to locate the throwing code.",
                 "Handle the failing state and add a focused regression test for it."]
    if 4 in signals or "form:" in text or "validation" in text:
        cause = "The observed form behavior did not meet its expected validation contract; the exact cause may be client-side validation, server validation, or their mismatch."
        fixes = ["Compare client and server validation rules for the reported input.",
                 "Return a clear field-level error and verify both invalid and valid submissions."]
    if 3 in signals or "broken:" in text:
        cause = "A page element or link was observed as broken. Confirm the destination/resource and inspect the response and browser trace."
        fixes = ["Verify the element's destination or resource URL and its response status.",
                 "Correct the route/reference and add a test that follows the link from its source page."]
    if "contrast" in text or "visual" in text or 5 in signals:
        comps.append("applitools")
    # A browser symptom does not justify replacing a site's framework.
    stack = "No framework migration is justified by this finding alone. Fix the demonstrated defect in the target's current stack; use source-repository analysis or profiling before recommending a stack change."
    refs = [dict(name=PUBLIC_METHODS[k][0], method=PUBLIC_METHODS[k][1], url=PUBLIC_METHODS[k][2])
            for k in dict.fromkeys(comps)]
    return {"likely_cause": cause, "recommended_fix": fixes,
            "stack_advice": stack, "competitor_methods": refs,
            "confidence_note": "Recommendations are heuristic triage based on recorded evidence, not a claim about a vendor's private implementation or proof of source-code root cause."}
