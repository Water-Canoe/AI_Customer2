from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import views
from app.api_dependencies import require_license
from app.schemas import BulkActionPreview, CustomerFollowStatusUpdate, TableUpdate
from app.services import account_actions, bulk_actions, deletion, job_queue, ops_visibility


router = APIRouter(prefix="/api", tags=["overview"])


@router.post("/overview/customers/{lead_id}/intent-analysis")
def analyze_overview_customer_intent(lead_id: int, run_now: bool = True) -> dict[str, object]:
    require_license()
    try:
        job = account_actions.create_customer_intent_analysis(lead_id, run_now=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now:
        job["runtime_job"] = job_queue.enqueue_ai_job(str(job["id"]))
    return job


@router.patch("/overview/customers/{lead_id}/follow-status")
def update_overview_customer_follow_status(
    lead_id: int,
    payload: CustomerFollowStatusUpdate,
) -> dict[str, object]:
    try:
        return account_actions.update_customer_follow_status(
            lead_id,
            payload.follow_status,
            payload.note,
            record_message_attempt=payload.record_message_attempt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/overview/accounts/{account_id}/customers/analyze")
def analyze_overview_account_customers(account_id: int, run_now: bool = True) -> dict[str, object]:
    require_license()
    try:
        result = account_actions.create_account_customer_intent_jobs(account_id, run_now=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now and result["job_ids"]:
        result["runtime_job"] = job_queue.enqueue(
            "account_customer_intent",
            entity_id=f"account:{account_id}",
            payload={"job_ids": result["job_ids"]},
            resource="ai",
            max_attempts=2,
        )
    return result


@router.post("/overview/accounts/{account_id}/customers/non-customers/delete")
def delete_overview_account_non_customers(account_id: int) -> dict[str, object]:
    try:
        return account_actions.delete_account_non_customers(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tables/{library}")
def list_table(
    library: str,
    status: str = Query(default=""),
    keyword: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    try:
        return views.list_library(library, status=status, keyword=keyword, page=page, page_size=page_size)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未知数据表") from exc


@router.patch("/tables/{library}/{row_id}")
def update_table_row(library: str, row_id: int, payload: TableUpdate) -> dict[str, object]:
    return views.update_library_row(library, row_id, payload.values)


@router.delete("/tables/{library}/{row_id}")
def delete_table_row(
    library: str,
    row_id: int,
    hard: bool | None = Query(default=None),
) -> dict[str, object]:
    return deletion.delete_library_row(library, row_id, hard)


@router.get("/overview/tree")
def overview_tree() -> list[dict[str, object]]:
    return views.overview_tree()


@router.get("/overview/children")
def overview_children(
    node_id: str = Query(...),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> dict[str, object]:
    try:
        return views.overview_children(node_id, page, page_size)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc.args[0])) from exc


@router.post("/overview/keywords/non-competitors/delete")
def delete_keyword_non_competitors(
    platform: str = Query(...),
    keyword: str = Query(...),
) -> dict[str, object]:
    return account_actions.delete_keyword_non_competitors(platform, keyword)


@router.post("/overview/keywords/analyze")
def analyze_keyword_accounts(
    platform: str = Query(...),
    keyword: str = Query(...),
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    result = account_actions.create_keyword_account_analysis_tasks(platform, keyword)
    if run_now and result["task_ids"]:
        account_ids = [int(item["account_id"]) for item in result.get("accounts", [])]
        result["runtime_job"] = job_queue.enqueue_account_analysis(
            account_ids,
            str(result["task_ids"][0]),
            kind="keyword_account_analysis",
        )
    return result


@router.post("/overview/keywords/find-customers")
def find_keyword_customers(
    platform: str = Query(...),
    keyword: str = Query(...),
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    result = account_actions.create_keyword_find_customer_task(platform, keyword)
    if run_now and result["task_ids"]:
        result["runtime_job"] = job_queue.enqueue_crawl_batch(result["task_ids"])
    return result


@router.delete("/overview/platforms/{platform}")
def delete_overview_platform(platform: str) -> dict[str, object]:
    return deletion.delete_overview_platform(platform)


@router.delete("/overview/keywords")
def delete_overview_keyword(
    platform: str = Query(...),
    keyword: str = Query(...),
) -> dict[str, object]:
    return deletion.delete_overview_keyword(platform, keyword)


@router.delete("/overview/accounts/{account_id}")
def delete_overview_account(account_id: int) -> dict[str, object]:
    return deletion.delete_overview_account(account_id)


@router.delete("/overview/customers/{lead_id}")
def delete_overview_customer(
    lead_id: int,
    source_account_id: int | None = Query(default=None),
) -> dict[str, object]:
    return deletion.delete_lead_customer(lead_id, source_account_id=source_account_id, source="overview_customer_delete")


@router.get("/workbench/actions")
def workbench_actions() -> dict[str, object]:
    return views.workbench_actions()


@router.get("/tombstones/summary")
def get_tombstone_summary() -> dict[str, object]:
    return ops_visibility.tombstone_summary()


@router.get("/tombstones")
def get_tombstones(
    entity_type: str = Query(default=""),
    platform: str = Query(default=""),
    source: str = Query(default=""),
    query: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    return ops_visibility.list_tombstones(
        entity_type=entity_type,
        platform=platform,
        source=source,
        query=query,
        page=page,
        page_size=page_size,
    )


@router.post("/bulk-actions/preview")
def bulk_action_preview(payload: BulkActionPreview) -> dict[str, object]:
    return bulk_actions.preview_bulk_action(payload)


@router.get("/platform-capabilities")
def platform_capabilities() -> list[dict[str, object]]:
    return views.platform_capabilities()


@router.get("/overview/node/{node_id:path}")
def overview_node(node_id: str) -> dict[str, object]:
    return views.overview_node(node_id)
