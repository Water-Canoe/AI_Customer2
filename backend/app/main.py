from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app import database
from app.schemas import AiBatchCreate, AiJobCreate, ClearDataRequest, SettingsUpdate, TableUpdate, TaskCreate
from app.services import ai_service, crawler_adapter, deletion, maintenance
from app import views


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    yield


app = FastAPI(title="AI拓客工具", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/tasks")
def create_task(payload: TaskCreate, background_tasks: BackgroundTasks) -> dict[str, object]:
    try:
        task = crawler_adapter.create_task(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(crawler_adapter.run_task, str(task["id"]))
    return task


@app.post("/api/accounts/{account_id}/profile-enrichment")
def create_profile_enrichment_task(account_id: int, background_tasks: BackgroundTasks) -> dict[str, object]:
    try:
        task = crawler_adapter.create_profile_enrichment_task(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(crawler_adapter.run_task, str(task["id"]))
    return task


@app.get("/api/tasks")
def list_tasks(include_archived: bool = False) -> list[dict[str, object]]:
    return crawler_adapter.list_tasks(include_archived)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, object]:
    task = crawler_adapter.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task["logs"] = crawler_adapter.list_task_logs(task_id)
    return task


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, object]:
    return crawler_adapter.cancel_task(task_id)


@app.post("/api/tasks/{task_id}/archive")
def archive_task(task_id: str) -> dict[str, object]:
    return crawler_adapter.archive_task(task_id)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, object]:
    return deletion.delete_task(task_id)


@app.get("/api/tables/{library}")
def list_table(
    library: str,
    status: str = Query(default=""),
    keyword: str = Query(default=""),
) -> dict[str, object]:
    try:
        return views.list_library(library, status=status, keyword=keyword)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未知数据表") from exc


@app.patch("/api/tables/{library}/{row_id}")
def update_table_row(library: str, row_id: int, payload: TableUpdate) -> dict[str, object]:
    return views.update_library_row(library, row_id, payload.values)


@app.delete("/api/tables/{library}/{row_id}")
def delete_table_row(
    library: str,
    row_id: int,
    hard: bool | None = Query(default=None),
) -> dict[str, object]:
    return deletion.delete_library_row(library, row_id, hard)


@app.get("/api/overview/tree")
def overview_tree() -> list[dict[str, object]]:
    return views.overview_tree()


@app.get("/api/overview/node/{node_id:path}")
def overview_node(node_id: str) -> dict[str, object]:
    return views.overview_node(node_id)


@app.post("/api/ai/jobs")
def create_ai_job(payload: AiJobCreate) -> dict[str, object]:
    return ai_service.create_ai_job(payload.target_type, payload.target_id, payload.run_now)


@app.post("/api/ai/jobs/batch")
def create_ai_jobs(payload: AiBatchCreate) -> list[dict[str, object]]:
    return ai_service.create_batch_jobs(payload.target_type, payload.target_ids, payload.run_now)


@app.get("/api/ai/jobs")
def list_ai_jobs() -> list[dict[str, object]]:
    return ai_service.list_ai_jobs()


@app.post("/api/ai/jobs/{job_id}/retry")
def retry_ai_job(job_id: str) -> dict[str, object]:
    return ai_service.retry_ai_job(job_id)


@app.get("/api/settings")
def get_settings() -> dict[str, object]:
    return views.get_settings()


@app.put("/api/settings")
def update_settings(payload: SettingsUpdate) -> dict[str, object]:
    return views.update_settings(payload.values)


@app.get("/api/settings/env-check")
def env_check() -> dict[str, object]:
    return views.environment_check()


@app.post("/api/settings/clear-data")
def clear_data(payload: ClearDataRequest) -> dict[str, object]:
    try:
        return maintenance.clear_all_data(payload.confirm)
    except (ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
