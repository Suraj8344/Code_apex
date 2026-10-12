"""
Stage 8 — Automated Benchmark Scorer.
Matches FiledBug reports against the planted-bug ground truth (bugs.json) and
computes Precision, Recall, F1, Reproducibility % and coverage — the honest
numbers incumbents never publish. Deterministic Python + structured matching.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core import config

SIGNAL_SYM = {1: "console", 2: "network", 3: "broken", 4: "form",
              5: "accessib", 6: "intent"}


def _norm(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def match_score(filed: dict, truth: dict) -> float:
    """0..1 similarity between a filed report and one ground-truth bug."""
    score = 0.0
    fs, ts = set(filed.get("signals", [])), set(truth.get("signals", []))
    if fs & ts:
        score += 0.45
    fr = (filed.get("route") or "").lower()
    tr = (truth.get("route") or "").lower().replace("*", "")
    if fr and tr and (fr in tr or tr in fr):
        score += 0.25
    fw = _norm(filed.get("title", "")) | _norm(filed.get("element_name", ""))
    tw = (_norm(truth.get("title", "")) | _norm(truth.get("trigger", ""))
          | _norm(truth.get("buggy", "")))
    overlap = len(fw & tw)
    score += min(0.30, overlap * 0.075)
    return round(score, 3)


def evaluate(run_dir: Path, bugs_path: Path | None = None,
             threshold: float = 0.50) -> dict:
    bugs_path = bugs_path or config.SAMPLE_APP_BUGS
    filed_path = Path(run_dir) / "bugs_filed.json"
    if not bugs_path.exists() or not filed_path.exists():
        return {"available": False}
    truth = json.loads(bugs_path.read_text())["bugs"]
    filed = json.loads(filed_path.read_text())

    tp_hits, used_truth = [], set()
    for f in filed:
        best, best_score = None, 0.0
        for t in truth:
            if t["id"] in used_truth:
                continue
            s = match_score(f, t)
            if s > best_score:
                best, best_score = t, s
        if best and best_score >= threshold:
            used_truth.add(best["id"])
            tp_hits.append({"filed": f["bug_id"], "truth": best["id"],
                            "score": best_score})

    tp = len(tp_hits)
    precision = tp / max(len(filed), 1)
    recall = tp / max(len(truth), 1)
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0)
    reproduced = sum(1 for f in filed
                     if f.get("reproduced_count", "0/3").split("/")[0] != "0")
    return {
        "available": True,
        "filed_reports": len(filed),
        "planted_bugs": len(truth),
        "true_positives": tp,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "reproducibility_pct": round(100 * reproduced / max(len(filed), 1), 1),
        "matches": tp_hits,
        "missed": [t["id"] for t in truth if t["id"] not in used_truth],
    }
