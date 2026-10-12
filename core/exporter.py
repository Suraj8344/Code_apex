"""
Stage 7b — Self-verifying test suite export.
[Jinja2 Template] passed journeys -> standalone pytest-playwright files
[pytest-playwright] RUN EXPORTED TESTS (no AI) — drop any file that fails.
100% of delivered tests run green on their own. Zero lock-in: plain code.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import config
from .contracts import TestCase


class Exporter:
    def __init__(self, run_dir: Path, target_url: str) -> None:
        self.run_dir = run_dir
        self.target_url = target_url.rstrip("/")
        self.jinja = Environment(
            loader=FileSystemLoader(str(config.JINJA_DIR)), autoescape=False)
        self.out_dir = run_dir / "exported_tests"
        self.out_dir.mkdir(exist_ok=True)

    @staticmethod
    def _slug(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:48]

    def export(self, passed: list[TestCase]) -> list[str]:
        files: list[str] = []
        tpl = self.jinja.get_template("exported_test.py.j2")
        for tc in passed:
            body = tpl.render(tc=tc, target_url=self.target_url,
                              slug=self._slug(tc.test_id + "_" + tc.title))
            f = self.out_dir / f"test_{self._slug(tc.test_id + '_' + tc.title)}.py"
            f.write_text(body)
            files.append(f.name)
        return files

    def self_verify(self, files: list[str]) -> tuple[list[str], str]:
        """Runs the exported suite; keeps only tests that pass 100% green."""
        if not files:
            return [], "no tests exported"
        cmd = ["python3", "-m", "pytest", str(self.out_dir), "-q",
               "--base-url", self.target_url, "-p", "no:cacheprovider",
               "--tb=line"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            log = proc.stdout + proc.stderr
        except Exception as exc:
            return [], f"pytest failed to run: {exc}"
        failed_blocks = re.findall(r"^(?:FAILED|ERROR)\s+(\S+)", log, re.M)
        keep: list[str] = []
        for name in files:
            if any(name.removesuffix(".py") in b for b in failed_blocks):
                (self.out_dir / name).rename(self.out_dir / (name + ".dropped"))
            else:
                keep.append(name)
        return keep, log[-2000:]
