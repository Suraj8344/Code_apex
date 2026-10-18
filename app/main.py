"""
QArena web application — FastAPI.
Replaces the spec's Streamlit with a hand-crafted field-guide UI:
  /            Stage 1 input form (expedition setup)
  /runs/{id}   live expedition view (SSE) -> findings board
  /api/...     run control, artifacts, benchmark, zip download
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
import secrets
import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from core import config
from core import coordination
from core.orchestrator import Orchestrator

ROOT = config.ROOT
app = FastAPI(title="QArena — Autonomous AI QA Engineer", docs_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "frontend" / "static"),
          name="static")
templates = Jinja2Templates(directory=ROOT / "frontend" / "templates")
orch = Orchestrator()
STORE = config.ARTIFACTS / "workspace.json"
STORE_LOCK = threading.Lock()
ROLES = {"frontend": "Frontend", "backend": "Backend", "qa": "QA Engineer",
         "devops": "DevOps", "lead": "Project Lead", "all": "Full report"}


@app.get("/health")
async def health():
    if coordination.enabled():
        try:
            coordination.redis_client().ping()
        except Exception:
            raise HTTPException(503, "Redis queue is unavailable")
        if not os.getenv("QA_S3_BUCKET"):
            raise HTTPException(503, "QA_S3_BUCKET must be configured for worker artifacts")
    return {"ok": True, "queue": "redis" if coordination.enabled() else "local"}


def read_store() -> dict:
    if coordination.enabled():
        try:
            client = coordination.redis_client()
            stored = client.get("qarena:workspace") if client else None
        except Exception as exc:
            raise HTTPException(503, "Shared Redis storage is unavailable") from exc
        if stored:
            try:
                return json.loads(stored)
            except json.JSONDecodeError:
                pass
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"projects": {}, "shares": {}}


def write_store(data: dict) -> None:
    if coordination.enabled():
        try:
            client = coordination.redis_client()
            if client:
                client.set("qarena:workspace", json.dumps(data))
                return
        except Exception as exc:
            raise HTTPException(503, "Shared Redis storage is unavailable") from exc
    STORE.parent.mkdir(parents=True, exist_ok=True)
    temp = STORE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(STORE)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48] or "project"


def bug_roles(bug: dict) -> set[str]:
    text = " ".join([str(bug.get("title", "")), str(bug.get("actual_behavior", "")),
                     str(bug.get("route", ""))]).lower()
    signals = set(bug.get("signals", []))
    roles = set()
    if any(x in text for x in ("layout", "css", "visual", "button", "label", "contrast", "accessib", "screen", "render")) or 5 in signals:
        roles.add("frontend")
    if any(x in text for x in ("api", "http", "server", "500", "request", "validation", "database", "backend")) or 2 in signals:
        roles.add("backend")
    if any(x in text for x in ("timeout", "deployment", "environment", "configuration", "build", "host")):
        roles.add("devops")
    if not roles or 4 in signals or 6 in signals:
        roles.add("qa")
    if any(x in text for x in ("flaky", "regression", "test", "reproduced")):
        roles.add("qa")
    roles.add("lead")
    return roles


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    owner: str = Field(min_length=1, max_length=80)


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(default="", max_length=254)
    role: str


class ShareCreate(BaseModel):
    role: str = "all"


# -------------------------------------------------- restart-resilient runs
def load_handle(run_id: str):
    """Return the live handle, or resurrect the run from its artifact dir so
    pages/events/zip keep working after a UI server restart."""
    h = orch.runs.get(run_id)
    if h:
        return h
    shared = coordination.get_run(run_id)
    if shared:
        if shared["result"] and shared["status"] in {"done", "error", "blocked"}:
            from core.orchestrator import RunHandle
            from core.contracts import RunResult
            h = RunHandle(run_id=run_id)
            h.run_dir = config.ARTIFACTS / f"run_{run_id}"
            h.result = RunResult(**shared["result"])
            h.history = [event for _, event in coordination.read_events(run_id)]
            orch.runs[run_id] = h
            return h
        events = [event for _, event in coordination.read_events(run_id)]
        payload = shared["payload"]
        return {"run_id": run_id, "status": shared["status"],
                "target_url": payload.get("url", ""), "project_id": shared["project_id"],
                "events": events, "result": shared["result"]}
    from core.orchestrator import RunHandle
    from core.contracts import RunResult
    run_dir = config.ARTIFACTS / f"run_{run_id}"
    results = run_dir / "results.json"
    if not results.exists():
        # Serverless instances do not share in-memory run handles. Keep a run
        # directory created by another instance visible as an explicit error
        # instead of returning a 404 and an endless EventSource reconnect loop.
        if run_dir.exists():
            log_path = run_dir / "run.log"
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
                      if line.strip()] if log_path.exists() else []
            return {"run_id": run_id, "status": "unavailable",
                    "message": "This run started on a different or expired serverless instance. Its live browser session cannot be resumed. Start a new run using the persistent worker deployment.",
                    "events": events}
        return None
    h = RunHandle(run_id=run_id)
    h.run_dir = run_dir
    h.result = RunResult(**json.loads(results.read_text()))
    h.project_id = h.result.project_id
    log = run_dir / "run.log"
    if log.exists():
        h.history = [json.loads(ln) for ln in log.read_text().splitlines()
                     if ln.strip()]
    orch.runs[run_id] = h
    return h


# ---------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Product homepage — what QArena is and how a hunt works."""
    return templates.TemplateResponse(request, "landing.html", {"projects": list(read_store()["projects"].values())})


@app.get("/expedition", response_class=HTMLResponse)
async def expedition_form(request: Request):
    # never prefill target or credentials — blank fields, honest placeholders
    return templates.TemplateResponse(request, "index.html",
                                      {"default_url": "", "default_desc": "",
                                       "projects": list(read_store()["projects"].values())})


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_page(request: Request, run_id: str):
    h = load_handle(run_id)
    if not h:
        return templates.TemplateResponse(request, "run_unavailable.html", {
            "run_id": run_id,
            "message": "This run ID is not available on this server. It may be an example link from an older deployment, or its saved run data may have been removed. Start a new expedition to create a fresh report.",
            "events": [],
        }, status_code=404)
    if isinstance(h, dict) and h["status"] == "unavailable":
        return templates.TemplateResponse(request, "run_unavailable.html", h)
    return templates.TemplateResponse(request, "run.html", {
        "run_id": run_id, "target": (h["target_url"] if isinstance(h, dict) else h.result.target_url if h.result else ""),
        "history": json.dumps(h["events"] if isinstance(h, dict) else h.history), "projects": list(read_store()["projects"].values()),
        "project_id": (h["project_id"] if isinstance(h, dict) else getattr(h, "project_id", h.result.project_id if h.result else "")),
    })


# ---------------------------------------------------------------- API
@app.post("/api/runs")
async def api_start(request: Request):
    body = await request.json()
    url = (body.get("url") or "").strip().rstrip("/")
    desc = (body.get("description") or "").strip()
    if not url.startswith("http") or not desc:
        raise HTTPException(400, "target url and description are required")
    # ---- input hygiene ------------------------------------------------------
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise HTTPException(400, "target must be a valid http(s) URL, "
                                 "e.g. https://your-app.com")
    if u.username or u.password:
        raise HTTPException(400, "do not embed credentials in the URL — "
                                 "use the optional login fields instead")
    if len(desc) > 4000:
        raise HTTPException(400, "description is too long (keep it under 4000 "
                                 "characters — business rules, not essays)")
    if len(body.get("email") or "") > 320 or len(body.get("password") or "") > 320:
        raise HTTPException(400, "credential fields are too long")
    try:
        active = coordination.active_count() if coordination.enabled() else sum(
            h.result and h.result.status == "running" for h in orch.runs.values())
    except Exception as exc:
        raise HTTPException(503, "Redis queue is unavailable") from exc
    if active >= int(__import__("os").getenv("QA_MAX_ACTIVE_RUNS", "2")):
        raise HTTPException(409, "an expedition is already running — start the "
                                 "next one when it finishes")
    project_id = (body.get("project_id") or "").strip()
    if project_id and project_id not in read_store()["projects"]:
        raise HTTPException(404, "project not found")
    email, password = (body.get("email") or "").strip(), (body.get("password") or "").strip()
    whitebox = body.get("whitebox") or None
    if coordination.enabled():
        if not os.getenv("QA_S3_BUCKET"):
            raise HTTPException(503, "Configure QA_S3_BUCKET for artifacts produced by the separate worker")
        import uuid
        run_id = uuid.uuid4().hex[:10]
        try:
            coordination.enqueue(run_id, {"url": url, "description": desc, "email": email,
                                          "password": password, "whitebox": whitebox,
                                          "project_id": project_id})
        except Exception as exc:
            raise HTTPException(503, f"Could not submit expedition to the browser queue: {exc}") from exc
    else:
        run_id = orch.start_run(url, desc, email=email, password=password,
                                whitebox=whitebox, project_id=project_id)
    if project_id:
        with STORE_LOCK:
            store = read_store()
            project = store["projects"].get(project_id)
            if project is not None:
                project.setdefault("runs", []).insert(0, run_id)
                write_store(store)
    return {"run_id": run_id, "watch": f"/runs/{run_id}"}


@app.get("/api/projects")
async def api_projects():
    return list(read_store()["projects"].values())


@app.post("/api/projects")
async def api_create_project(payload: ProjectCreate):
    with STORE_LOCK:
        store = read_store()
        project_id = slug(payload.name)
        if project_id in store["projects"]:
            project_id = f"{project_id}-{secrets.token_hex(2)}"
        project = {"id": project_id, "name": payload.name.strip(), "owner": payload.owner.strip(),
                   "members": [{"id": secrets.token_hex(5), "name": payload.owner.strip(),
                                "email": "", "role": "lead"}], "runs": [],
                   "created_at": int(time.time())}
        store["projects"][project_id] = project
        write_store(store)
    return project


@app.post("/api/projects/{project_id}/members")
async def api_add_member(project_id: str, payload: MemberCreate):
    if payload.role not in ROLES or payload.role == "all":
        raise HTTPException(400, "choose frontend, backend, qa, devops, or lead")
    with STORE_LOCK:
        store = read_store()
        project = store["projects"].get(project_id)
        if not project:
            raise HTTPException(404, "project not found")
        member = {"id": secrets.token_hex(5), "name": payload.name.strip(),
                  "email": payload.email.strip(), "role": payload.role}
        project["members"].append(member)
        write_store(store)
    return member


@app.get("/api/projects/{project_id}/runs")
async def api_project_runs(project_id: str):
    project = read_store()["projects"].get(project_id)
    if not project:
        raise HTTPException(404, "project not found")
    results = []
    for run_id in project.get("runs", []):
        handle = load_handle(run_id)
        if isinstance(handle, dict):
            results.append({"run_id": run_id, "target_url": handle.get("target_url", ""),
                            "status": handle["status"], "bugs": 0})
        elif handle and handle.result:
            results.append({"run_id": run_id, "target_url": handle.result.target_url,
                            "status": handle.result.status, "bugs": len(handle.result.filed_bugs)})
    return results


@app.get("/api/runs/{run_id}/report/{role}")
async def api_role_report(run_id: str, role: str):
    if role not in ROLES:
        raise HTTPException(404, "unknown report role")
    handle = load_handle(run_id)
    if isinstance(handle, dict):
        raise HTTPException(409 if handle.get("status") != "unavailable" else 410,
                            handle.get("message", "Report is not ready until the run finishes"))
    if handle and handle.result and handle.result.status == "blocked":
        raise HTTPException(409, handle.result.access_blocked_reason or
                            "The target blocked browser access; this run cannot provide a bug report.")
    if not handle or not handle.result:
        if isinstance(handle, dict) and handle.get("status") != "unavailable":
            return {"run_id": run_id, "status": handle["status"],
                    "target_url": handle["target_url"], "project_id": handle["project_id"],
                    "filed_bugs": [], "flaky": [], "exported_tests": [],
                    "coverage": {}, "benchmark": {}}
        raise HTTPException(404, "unknown run")
    result = handle.result.model_dump()
    # RunResult.model_dump() already converts nested FiledBug instances into
    # dictionaries. Calling model_dump() again raises AttributeError and made
    # both the role-view API and report download return HTTP 500.
    bugs = result.get("filed_bugs", [])
    if role != "all":
        bugs = [b for b in bugs if role in bug_roles(b)]
    from core.recommendations import advise
    models = {item.bug_id: item for item in handle.result.filed_bugs}
    for bug in bugs:
        model = models.get(bug.get("bug_id"))
        if model:
            bug["recommendations"] = advise(model)
    return {"project_id": handle.result.project_id, "role": role,
            "role_name": ROLES[role], "run_id": run_id, "target_url": result["target_url"],
            "status": result["status"], "bugs": bugs, "flaky": result.get("flaky", []),
            "coverage": result.get("coverage", {}), "benchmark": result.get("benchmark", {}),
            "exported_tests": result.get("exported_tests", [])}


@app.post("/api/runs/{run_id}/share")
async def api_create_share(run_id: str, payload: ShareCreate):
    if payload.role not in ROLES:
        raise HTTPException(400, "unknown report role")
    if not load_handle(run_id):
        raise HTTPException(404, "unknown run")
    handle = load_handle(run_id)
    if isinstance(handle, dict):
        raise HTTPException(409, "Wait for the expedition to finish before sharing its report")
    token = secrets.token_urlsafe(18)
    with STORE_LOCK:
        store = read_store()
        store["shares"][token] = {"run_id": run_id, "role": payload.role,
                                  "created_at": int(time.time())}
        write_store(store)
    return {"url": f"/share/{token}"}


@app.get("/api/share/{token}")
async def api_read_share(token: str):
    share = read_store()["shares"].get(token)
    if not share:
        raise HTTPException(404, "share link not found")
    report = await api_role_report(share["run_id"], share["role"])
    report.pop("project_id", None)
    return report


@app.get("/share/{token}", response_class=HTMLResponse)
async def share_page(request: Request, token: str):
    share = read_store()["shares"].get(token)
    if not share:
        raise HTTPException(404, "share link not found")
    return templates.TemplateResponse(request, "share.html", {"token": token})


@app.get("/api/runs/{run_id}/events")
async def api_events(request: Request, run_id: str):
    h = load_handle(run_id)
    if not h:
        raise HTTPException(404, "unknown run")
    if isinstance(h, dict):
        if h["status"] == "unavailable":
            return JSONResponse({"status": "unavailable", "message": h["message"],
                                 "events": h["events"]}, status_code=410)

        async def shared_stream():
            last_id = "0-0"
            if request.headers.get("last-event-id"):
                last_id = request.headers["last-event-id"]
            else:
                initial = coordination.read_events(run_id)
                result = coordination.get_run(run_id)
                if initial:
                    last_id = initial[-1][0]
                yield f"data: {json.dumps({'type':'snapshot','history':[event for _, event in initial],'result':result.get('result') if result else {}})}\n\n"
            while True:
                events = await asyncio.to_thread(coordination.wait_events, run_id, last_id, 15)
                if not events:
                    yield 'data: {"type":"ping"}\n\n'
                for event_id, event in events:
                    last_id = event_id
                    yield f"id: {event_id}\ndata: {json.dumps(event)}\n\n"
                    if event.get("type") == "done":
                        return
                current = coordination.get_run(run_id)
                if current and current["status"] in {"done", "error", "blocked"}:
                    if current["status"] == "error" and not current["result"]:
                        yield f"data: {json.dumps({'type':'error','msg':'Browser worker job failed before QArena could save a result'})}\n\n"
                        yield f"data: {json.dumps({'type':'done','status':'error','result':{'status':'error','error':'Browser worker job failed'}})}\n\n"
                    return

        return StreamingResponse(shared_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    async def stream():
        yield f"data: {json.dumps({'type':'snapshot','history':h.history,'result':h.result.model_dump() if h.result else {}})}\n\n"
        while True:
            try:
                ev = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: h.events.get(timeout=20))
                yield f"data: {json.dumps(ev)}\n\n"
                if ev.get("type") == "done":
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'
            except Exception:
                break

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/result")
async def api_result(run_id: str):
    h = load_handle(run_id)
    if not h or not h.result:
        if isinstance(h, dict):
            if h.get("status") == "unavailable":
                return JSONResponse(h, status_code=410)
            return JSONResponse({"run_id": run_id, "target_url": h["target_url"],
                                 "project_id": h["project_id"], "status": h["status"]})
        raise HTTPException(404, "unknown run")
    result = h.result.model_dump()
    from core.recommendations import advise
    for bug, model in zip(result.get("filed_bugs", []), h.result.filed_bugs):
        bug["recommendations"] = advise(model)
    return JSONResponse(result)


@app.get("/api/runs/{run_id}/bugs_filed")
async def api_bugs(run_id: str):
    handle = load_handle(run_id)
    if handle and not isinstance(handle, dict) and handle.result:
        return JSONResponse([bug.model_dump() for bug in handle.result.filed_bugs])
    p = config.ARTIFACTS / f"run_{run_id}" / "bugs_filed.json"
    if not p.exists():
        return JSONResponse([])
    return JSONResponse(json.loads(p.read_text()))


@app.get("/artifacts/{run_id}/{path:path}")
async def api_artifact(run_id: str, path: str):
    base = (config.ARTIFACTS / f"run_{run_id}").resolve()
    p = (base / path).resolve()
    if not p.is_relative_to(base):
        raise HTTPException(404, "not found")
    if not p.exists() and os.getenv("QA_S3_BUCKET"):
        try:
            import boto3
            key = f"{os.getenv('QA_S3_PREFIX', 'qarena').strip('/')}/run_{run_id}/{Path(path).as_posix()}"
            with tempfile.NamedTemporaryFile(delete=False) as temp:
                temp_path = Path(temp.name)
            boto3.client("s3", endpoint_url=os.getenv("QA_S3_ENDPOINT_URL") or None,
                         region_name=os.getenv("AWS_REGION") or os.getenv("QA_S3_REGION") or None).download_file(
                             os.environ["QA_S3_BUCKET"], key, str(temp_path))
            from starlette.background import BackgroundTask
            return FileResponse(temp_path, filename=Path(path).name,
                                headers={"Cache-Control": "no-store"},
                                background=BackgroundTask(temp_path.unlink, missing_ok=True))
        except Exception:
            raise HTTPException(404, "artifact not found")
    if not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.get("/api/runs/{run_id}/zip")
async def api_zip(run_id: str):
    import asyncio
    h = load_handle(run_id)
    if coordination.enabled():
        run_data = coordination.get_run(run_id)
        if not run_data:
            raise HTTPException(404, "unknown run")
        if run_data["status"] not in {"done", "error"}:
            raise HTTPException(409, "Evidence pack will be ready when the browser worker finishes")
        return await api_s3_zip(run_id)
    if isinstance(h, dict):
        if h.get("status") == "unavailable":
            raise HTTPException(410, h["message"])
        if os.getenv("QA_S3_BUCKET"):
            return await api_s3_zip(run_id)
        raise HTTPException(503, "Worker evidence is not on this web instance. Configure QA_S3_BUCKET for shared artifact storage.")
    if not h or not h.run_dir:
        raise HTTPException(404, "unknown run")
    archive = Path(str(h.run_dir) + ".zip")
    done = h.result is not None and h.result.status in ("done", "error")
    # finished runs: build the evidence pack ONCE, then serve the cached file;
    # running runs: always re-zip (fresh data) — never block the event loop
    if done and archive.exists():
        return FileResponse(archive, filename=f"qarena_run_{run_id}.zip")
    if archive.exists():
        archive.unlink()
    made = await asyncio.to_thread(orch.zip_run, run_id)
    if not made or not archive.exists():
        raise HTTPException(404, "run has no artifacts yet")
    return FileResponse(archive, filename=f"qarena_run_{run_id}.zip")


@app.get("/api/runs/{run_id}/report/{role}/download")
async def download_role_report(run_id: str, role: str):
    report = await api_role_report(run_id, role)
    lines = [f"# {report.get('role_name', ROLES[role])} QA report", "",
             f"- Target: {report.get('target_url', 'N/A')}",
             f"- Status: {report.get('status', 'unknown')}",
             f"- Findings: {len(report.get('bugs', []))}", ""]
    for bug in report.get("bugs", []):
        lines.extend([f"## {bug.get('bug_id', 'Finding')}: {bug.get('title', 'Untitled')}", "",
                      f"**Severity:** {bug.get('severity', 'Unclassified')}  ",
                      f"**Location:** {bug.get('route', 'N/A')} · {bug.get('journey', 'N/A')}  ",
                      f"**Reproduced:** {bug.get('reproduced_count', 'N/A')}", "",
                      f"**Expected:** {bug.get('expected_behavior', 'N/A')}", "",
                      f"**Actual:** {bug.get('actual_behavior', 'N/A')}", "", "Steps to reproduce:"])
        lines.extend(f"{i}. {step}" for i, step in enumerate(bug.get("steps_to_reproduce", []), 1))
        recommendations = bug.get("recommendations", {})
        if recommendations:
            lines.extend(["", "### Likely cause and recommended fix", "",
                          recommendations.get("likely_cause", "")])
            lines.extend(f"- {step}" for step in recommendations.get("recommended_fix", []))
            lines.extend(["", recommendations.get("stack_advice", ""), "",
                          recommendations.get("confidence_note", "")])
            for ref in recommendations.get("competitor_methods", []):
                lines.append(f"- Public method reference: [{ref['name']}]({ref['url']}) — {ref['method']}")
        lines.append("")
    filename = f"qarena_{run_id}_{role}_report.md"
    return Response(content="\n".join(lines), media_type="text/markdown",
                    headers={"Cache-Control": "no-store",
                             "Content-Disposition": f'attachment; filename="{filename}"'})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=config.UI_PORT)
