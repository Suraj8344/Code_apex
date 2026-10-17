"""Redis queue and run-state helpers shared by the web process and workers."""
from __future__ import annotations

import json
import os

RUN_TTL_SECONDS = int(os.getenv("QA_RUN_TTL_SECONDS", str(30 * 24 * 60 * 60)))


def enabled() -> bool:
    return os.getenv("QUEUE_MODE", "local").lower() == "redis"


def redis_client():
    if not enabled():
        return None
    import redis
    return redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True,
                                socket_connect_timeout=3, socket_timeout=5)


def enqueue(run_id: str, payload: dict) -> None:
    from rq import Queue
    from .config import QUEUE_NAME
    client = redis_client()
    if client is None:
        raise RuntimeError("REDIS_URL is required when QUEUE_MODE=redis")
    client.hset(f"qarena:run:{run_id}", mapping={
        "status": "queued", "payload": json.dumps(payload), "result": "",
        "project_id": payload.get("project_id", "")})
    client.expire(f"qarena:run:{run_id}", RUN_TTL_SECONDS)
    client.xadd(f"qarena:events:{run_id}", {"data": json.dumps({
        "type": "stage", "stage": 1, "name": "Queued for browser worker"})}, maxlen=5000)
    client.expire(f"qarena:events:{run_id}", RUN_TTL_SECONDS)
    Queue(QUEUE_NAME, connection=client).enqueue(
        "core.worker.execute_run", run_id, payload,
        job_id=run_id, result_ttl=86400, failure_ttl=604800,
        job_timeout=int(os.getenv("QA_JOB_TIMEOUT", "900")))


def save_result(run_id: str, result: dict) -> None:
    client = redis_client()
    if client:
        client.hset(f"qarena:run:{run_id}", mapping={
            "status": result.get("status", "error"), "result": json.dumps(result),
            "project_id": result.get("project_id", "")})
        client.expire(f"qarena:run:{run_id}", RUN_TTL_SECONDS)


def publish_event(run_id: str, event: dict) -> None:
    client = redis_client()
    if client:
        client.xadd(f"qarena:events:{run_id}", {"data": json.dumps(event)}, maxlen=5000)
        client.expire(f"qarena:events:{run_id}", RUN_TTL_SECONDS)


def get_run(run_id: str) -> dict | None:
    client = redis_client()
    if not client:
        return None
    values = client.hgetall(f"qarena:run:{run_id}")
    if not values:
        return None
    if values.get("status") in {"queued", "started", "deferred"}:
        job_id = run_id
        try:
            from rq.job import Job
            job = Job.fetch(job_id, connection=client)
            rq_status = job.get_status(refresh=True)
            if rq_status in {"started", "finished", "failed"}:
                status = "running" if rq_status == "started" else "done" if rq_status == "finished" else "error"
                client.hset(f"qarena:run:{run_id}", "status", status)
                values["status"] = status
        except Exception:
            pass
    result = None
    if values.get("result"):
        try:
            result = json.loads(values["result"])
        except json.JSONDecodeError:
            pass
    payload = {}
    try:
        payload = json.loads(values.get("payload", "{}"))
    except json.JSONDecodeError:
        pass
    return {"status": values.get("status", "queued"), "result": result,
            "payload": payload, "project_id": values.get("project_id", "")}


def read_events(run_id: str, start: str = "-") -> list[tuple[str, dict]]:
    client = redis_client()
    if not client:
        return []
    rows = client.xrange(f"qarena:events:{run_id}", min=start, max="+", count=5000)
    out = []
    for event_id, fields in rows:
        try:
            out.append((event_id, json.loads(fields["data"])))
        except (KeyError, json.JSONDecodeError):
            continue
    return out


def wait_events(run_id: str, last_id: str, timeout: int = 15) -> list[tuple[str, dict]]:
    client = redis_client()
    if not client:
        return []
    rows = client.xread({f"qarena:events:{run_id}": last_id},
                        count=100, block=timeout * 1000)
    out = []
    for _, entries in rows:
        for event_id, fields in entries:
            try:
                out.append((event_id, json.loads(fields["data"])))
            except (KeyError, json.JSONDecodeError):
                continue
    return out


def active_count() -> int:
    client = redis_client()
    if not client:
        return 0
    from rq import Queue
    from rq.registry import StartedJobRegistry
    from .config import QUEUE_NAME
    queue = Queue(QUEUE_NAME, connection=client)
    return queue.count + StartedJobRegistry(QUEUE_NAME, connection=client).count
