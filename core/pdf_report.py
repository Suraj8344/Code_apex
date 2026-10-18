"""Create a human-readable PDF summary for a completed QArena expedition."""
from __future__ import annotations

from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)


INK = colors.HexColor("#20251f")
GREEN = colors.HexColor("#285b3f")
AMBER = colors.HexColor("#b97709")
PALE = colors.HexColor("#f7f3e9")


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    # Paragraph interprets markup. Escape all run-provided values before rendering.
    return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), style)


def create_pdf_report(result, output: Path) -> Path:
    """Write the expedition result as a PDF and return its path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"],
                              fontName="Helvetica-Bold", fontSize=23, leading=28,
                              textColor=INK, alignment=TA_CENTER, spaceAfter=5))
    styles.add(ParagraphStyle(name="Subtle", parent=styles["Normal"],
                              fontSize=9, leading=13, textColor=colors.HexColor("#5c625b")))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"],
                              fontSize=14, leading=18, textColor=GREEN, spaceBefore=12,
                              spaceAfter=7, keepWithNext=True))
    styles.add(ParagraphStyle(name="BodySmall", parent=styles["BodyText"],
                              fontSize=9, leading=13, spaceAfter=4, wordWrap="CJK"))
    styles.add(ParagraphStyle(name="BugTitle", parent=styles["Heading3"],
                              fontSize=11, leading=14, textColor=INK, spaceAfter=4))

    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=18*mm,
                            leftMargin=18*mm, topMargin=18*mm, bottomMargin=17*mm,
                            title=f"QArena expedition {result.run_id}",
                            author="QArena")
    story = [Paragraph("QArena", styles["ReportTitle"]),
             _paragraph("AUTOMATED QA EXPEDITION REPORT", styles["Subtle"]),
             Spacer(1, 8), _paragraph(result.target_url, styles["Heading2"]),
             _paragraph(f"Run {result.run_id}  |  Status: {result.status.upper()}  |  Mode: {result.llm_mode}", styles["Subtle"]),
             Spacer(1, 12)]

    metrics = [
        ["Pages mapped", "Tests planned", "Tests passed", "Steps run"],
        [str(result.states_found), str(result.tests_planned), str(result.tests_passed), str(result.steps_executed)],
        ["Confirmed bugs", "Unverified", "LLM tokens", "Project"],
        [str(len(result.filed_bugs)), str(len(result.flaky)), str(result.tokens_used), result.project_id or "Standalone"],
    ]
    table = Table(metrics, colWidths=[42*mm]*4, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 2), (-1, 2), GREEN), ("TEXTCOLOR", (0, 2), (-1, 2), colors.white),
        ("BACKGROUND", (0, 1), (-1, 1), PALE), ("BACKGROUND", (0, 3), (-1, 3), PALE),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#c7c2b7")),
        ("INNERGRID", (0, 0), (-1, -1), .35, colors.HexColor("#c7c2b7")),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([table, Paragraph("Application overview", styles["Section"]),
                  _paragraph(result.description or "No application description provided.", styles["BodySmall"])])
    if result.tokens_used == 0 and "heuristic" in result.llm_mode.lower():
        story.append(_paragraph(
            "AI was not enabled for this run because no provider API key was configured (or offline mode was selected). Zero tokens means no LLM requests were made; route discovery and browser checks ran with QArena's deterministic rules.",
            styles["BodySmall"]))
    elif result.tokens_used == 0:
        story.append(_paragraph(
            "The provider did not report token usage for this run; the displayed zero may reflect missing usage metadata.",
            styles["BodySmall"]))

    if result.error:
        story.extend([Paragraph("Run status and limitations", styles["Section"]),
                      _paragraph(f"The expedition ended before the normal pipeline completed. Error: {result.error}", styles["BodySmall"])])
        if not result.states_found and not result.tests_planned and not result.steps_executed:
            story.append(_paragraph("No application behavior was tested. Zero bug findings in this run are inconclusive and must not be interpreted as a clean bill of health.", styles["BodySmall"]))
    if result.access_blocked_reason:
        story.extend([Paragraph("Access blocked — result inconclusive", styles["Section"]),
                      _paragraph(result.access_blocked_reason, styles["BodySmall"]),
                      _paragraph("No bug findings are reported because the browser did not reach the target application. Resolve the access challenge or use an authorized staging environment, then run QArena again.", styles["BodySmall"])])

    story.append(Paragraph("Confirmed findings", styles["Section"]))
    if not result.filed_bugs:
        if result.access_blocked_reason:
            story.append(_paragraph("No application bugs could be confirmed because access was blocked. This result is not evidence that the site is bug-free.", styles["BodySmall"]))
        else:
            story.append(_paragraph("No bugs were confirmed during this expedition. This reflects only the pages and journeys reached in this run.", styles["BodySmall"]))
    for bug in result.filed_bugs:
        parts = [Paragraph(escape(f"{bug.bug_id}: {bug.title}"), styles["BugTitle"]),
                 _paragraph(f"Severity: {bug.severity}  |  Reproduced: {bug.reproduced_count}  |  Route: {bug.route or 'N/A'}", styles["Subtle"]),
                 _paragraph(f"Expected: {bug.expected_behavior}", styles["BodySmall"]),
                 _paragraph(f"Observed: {bug.actual_behavior}", styles["BodySmall"])]
        if bug.steps_to_reproduce:
            parts.append(_paragraph("Reproduction steps: " + "; ".join(bug.steps_to_reproduce), styles["BodySmall"]))
        screenshot = (output.parent / (bug.evidence or {}).get("screenshot", "")).resolve()
        if (bug.evidence or {}).get("screenshot") and screenshot.is_relative_to(output.parent.resolve()) and screenshot.is_file():
            parts.extend([Spacer(1, 4),
                          Image(str(screenshot), width=170*mm, height=86*mm,
                                kind="proportional"),
                          _paragraph("Evidence screenshot captured during this finding.", styles["Subtle"])])
        from .recommendations import advise
        recommendation = advise(bug)
        parts.extend([Paragraph("Likely cause and suggested remediation", styles["BugTitle"]),
                      _paragraph(recommendation["likely_cause"], styles["BodySmall"])])
        parts.extend(_paragraph(f"- {step}", styles["BodySmall"])
                     for step in recommendation["recommended_fix"])
        parts.append(_paragraph(recommendation["stack_advice"], styles["BodySmall"]))
        parts.append(_paragraph(recommendation["confidence_note"], styles["Subtle"]))
        for reference in recommendation["competitor_methods"]:
            safe_url = escape(reference["url"], quote=True)
            safe_name = escape(reference["name"])
            parts.append(Paragraph(
                f'<link href="{safe_url}" color="#285b3f">{safe_name}</link>: '
                f'{escape(reference["method"])}', styles["BodySmall"]))
        story.append(KeepTogether(parts))

    story.append(Paragraph("Unverified observations", styles["Section"]))
    if not result.flaky:
        story.append(_paragraph("No unverified observations were recorded.", styles["BodySmall"]))
    else:
        for item in result.flaky:
            story.append(_paragraph(f"{item.summary} (reproduced {item.reproduced}; {item.status})", styles["BodySmall"]))

    story.append(Paragraph("Coverage and benchmark", styles["Section"]))
    coverage = result.coverage or {}
    benchmark = result.benchmark or {}
    lines = [f"Route coverage: {coverage.get('route_coverage', 'N/A')}% ({coverage.get('routes_tested', 0)}/{coverage.get('routes_total', 0)} routes)",
             f"Element coverage: {coverage.get('element_coverage', 'N/A')}% ({coverage.get('elements_exercised', 0)}/{coverage.get('elements_total', 0)} elements)"]
    if benchmark:
        lines.append("Benchmark: " + "; ".join(f"{k}: {v}" for k, v in benchmark.items()))
    else:
        lines.append("Benchmark: unavailable for this run.")
    story.extend(_paragraph(line, styles["BodySmall"]) for line in lines)

    story.append(Paragraph("Stack and run-time guidance", styles["Section"]))
    story.append(_paragraph(
        "A browser-observed defect alone is not evidence that the target needs a framework migration. Fix the demonstrated cause in the existing stack; use repository inspection or profiling before proposing React, Next.js, Python, or a replacement architecture.",
        styles["BodySmall"]))
    timings = result.stage_timings or {}
    if timings:
        slowest, seconds = max(timings.items(), key=lambda item: item[1])
        story.append(_paragraph(
            f"Longest measured pipeline stage: {slowest} ({seconds:.1f} seconds). Optimize this measured stage first; avoid changing the target application's stack based on QArena's own run time.",
            styles["BodySmall"]))
    else:
        story.append(_paragraph("No stage timings were recorded, so a run-time bottleneck cannot be identified from this report.", styles["BodySmall"]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d6d1c5"))
        canvas.line(18*mm, 13*mm, A4[0]-18*mm, 13*mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666a63"))
        canvas.drawString(18*mm, 8*mm, f"QArena | Run {result.run_id}")
        canvas.drawRightString(A4[0]-18*mm, 8*mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output
