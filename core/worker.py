"""RQ worker entry point: `rq worker qarena` from the repository root."""
from __future__ import annotations

from .orchestrator import Orchestrator


def execute_run(run_id: str, payload: dict) -> None:
    Orchestrator().run_job(run_id, payload)
