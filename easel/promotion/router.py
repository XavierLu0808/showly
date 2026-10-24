"""产品推广平台 API 路由（挂载于 /api/promotion）。"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import db, jobs, service
from .checks import run_checks
from .schemas import (
    AssetInput, CampaignCreate, CampaignUpdate, ContentCreate, ContentEdit,
    FactInput, FactUpdate, MetricImport, MetricInput, ProductCreate,
    ProductUpdate, PublicationInput, ReviewDecision, TrackingLinkInput,
    VersionCreate,
)

router = APIRouter(prefix="/api/promotion", tags=["promotion"])

UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
               ".mp4", ".mov", ".webm", ".m4v", ".mp3", ".wav", ".m4a",
               ".pdf", ".md", ".txt", ".docx", ".csv", ".json", ".zip"}
MAX_UPLOAD_MB = 200
OUTPUTS_DIR = service.OUTPUTS_DIR


def _status_for(exc: service.PromotionError) -> int:
    return 404 if "不存在" in str(exc) else 400


def ensure_ready() -> None:
    """初始化数据库并恢复中断任务（web/app.py 启动时调用一次，幂等）。"""
    db.init_db()
    jobs.recover_interrupted_jobs()


# ---------------------------------------------------------------- 产品

@router.get("/products")
def api_list_products():
    return {"ok": True, "products": service.list_products()}


@router.post("/products")
def api_create_product(payload: ProductCreate):
    try:
        return {"ok": True, "product": service.create_product(payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.get("/products/{product_id}")
def api_get_product(product_id: str):
    try:
        return {"ok": True, "product": service.get_product(product_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.put("/products/{product_id}")
def api_update_product(product_id: str, payload: ProductUpdate):
    try:
        return {"ok": True, "product": service.update_product(product_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/products/{product_id}/facts")
def api_add_fact(product_id: str, payload: FactInput):
    try:
        return {"ok": True, "fact": service.add_fact(product_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.put("/products/{product_id}/facts/{fact_id}")
def api_update_fact(product_id: str, fact_id: str, payload: FactUpdate):
    try:
        return {"ok": True, "fact": service.update_fact(product_id, fact_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/products/{product_id}/versions")
def api_create_version(product_id: str, payload: VersionCreate):
    try:
        return {"ok": True, "version": service.create_version(product_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/products/{product_id}/versions/{version_id}/confirm")
def api_confirm_version(product_id: str, version_id: str):
    try:
        return {"ok": True, "version": service.confirm_version(product_id, version_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


# ---------------------------------------------------------------- 素材

@router.post("/products/{product_id}/assets")
def api_add_asset(product_id: str, payload: AssetInput):
    try:
        return {"ok": True, "asset": service.add_asset(product_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/products/{product_id}/assets/upload")
async def api_upload_assets(product_id: str, files: list[UploadFile] = File(...)):
    """上传素材文件到 outputs/_promotion/assets/<product>/，返回可登记的相对路径。"""
    try:
        service.get_product(product_id)
    except service.PromotionError as exc:
        raise HTTPException(404, str(exc)) from None
    batch = time_bundle()
    dest = OUTPUTS_DIR / "_promotion" / "assets" / product_id / batch
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    cap = MAX_UPLOAD_MB * 1024 * 1024
    for f in files:
        name = Path(f.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXTS:
            raise HTTPException(400, f"不支持的文件类型：{ext or name}")
        target = dest / name
        stem, suffix = target.stem, target.suffix
        counter = 1
        while target.exists():
            target = dest / f"{stem}-{counter}{suffix}"
            counter += 1
        size = 0
        with open(target, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > cap:
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, f"{name} 超过 {MAX_UPLOAD_MB}MB 上限")
                out.write(chunk)
        rel = str(target.relative_to(OUTPUTS_DIR)).replace("\\", "/")
        saved.append({"name": name, "path": rel})
    return {"ok": True, "files": saved}


def time_bundle() -> str:
    import time
    return time.strftime("%Y%m%d-") + uuid.uuid4().hex[:6]


@router.get("/products/{product_id}/assets")
def api_list_assets(product_id: str):
    try:
        return {"ok": True, "assets": service.list_assets(product_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.delete("/products/{product_id}/assets/{asset_id}")
def api_delete_asset(product_id: str, asset_id: str):
    try:
        return service.delete_asset(product_id, asset_id)
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


# ---------------------------------------------------------------- 活动

@router.get("/campaigns")
def api_list_campaigns(product_id: str | None = None):
    try:
        return {"ok": True, "campaigns": service.list_campaigns(product_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/campaigns")
def api_create_campaign(payload: CampaignCreate):
    try:
        return {"ok": True, "campaign": service.create_campaign(payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.get("/campaigns/{campaign_id}")
def api_get_campaign(campaign_id: str):
    try:
        return {"ok": True, "campaign": service.get_campaign(campaign_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.put("/campaigns/{campaign_id}")
def api_update_campaign(campaign_id: str, payload: CampaignUpdate):
    try:
        return {"ok": True, "campaign": service.update_campaign(campaign_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/campaigns/{campaign_id}/jobs/{job_type}")
def api_start_campaign_job(campaign_id: str, job_type: str):
    """按任务类型启动活动级任务：brief（简报）/ angles（宣传角度）。"""
    try:
        service.get_campaign(campaign_id)
        job = jobs.create_job(job_type, campaign_id=campaign_id)
        return {"ok": True, "job": job}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/campaigns/{campaign_id}/confirm-plan")
def api_confirm_plan(campaign_id: str):
    """确认角度与清单：把已选 angles 生成为内容任务清单（contents）。"""
    try:
        return {"ok": True, "created": service.confirm_plan(campaign_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


# ---------------------------------------------------------------- 内容

@router.get("/campaigns/{campaign_id}/contents")
def api_list_contents(campaign_id: str):
    try:
        return {"ok": True, "contents": service.list_contents(campaign_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/contents")
def api_create_content(payload: ContentCreate):
    try:
        return {"ok": True, "content": service.create_content(payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.get("/contents/{content_id}")
def api_get_content(content_id: str):
    try:
        return {"ok": True, "content": service.get_content(content_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.put("/contents/{content_id}")
def api_edit_content(content_id: str, payload: ContentEdit):
    try:
        return {"ok": True, "content": service.edit_content(content_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/contents/{content_id}/jobs/{job_type}")
def api_start_content_job(content_id: str, job_type: str):
    """内容级任务：content_draft（生成草稿）/ checks（内容检查）。"""
    try:
        service.get_content(content_id)
        job = jobs.create_job(job_type, content_id=content_id)
        return {"ok": True, "job": job}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/contents/{content_id}/check")
def api_run_checks(content_id: str):
    """同步执行内容检查（小任务，直接跑规则）。"""
    try:
        content = service.get_content(content_id)
        latest = content["revisions"][-1]
        issues = run_checks(content, latest)
        service.record_check(content_id, issues, revision_id=latest["id"])
        return {"ok": True, "issues": issues, "content": service.get_content(content_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/contents/{content_id}/review")
def api_review_content(content_id: str, payload: ReviewDecision):
    try:
        if payload.revision_id is None:
            raise service.PromotionError("请指定要审核的内容修订 revision_id")
        return {"ok": True, "content": service.approve_content(content_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


# ---------------------------------------------------------------- 任务

@router.get("/jobs")
def api_list_jobs(campaign_id: str | None = None, content_id: str | None = None):
    return {"ok": True, "jobs": jobs.list_jobs(campaign_id, content_id)}


@router.get("/jobs/{job_id}")
def api_get_job(job_id: str):
    try:
        return {"ok": True, "job": jobs.get_job(job_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str):
    try:
        return {"ok": True, "job": jobs.cancel_job(job_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/jobs/{job_id}/retry")
def api_retry_job(job_id: str):
    try:
        return {"ok": True, "job": jobs.retry_job(job_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


# ---------------------------------------------------------------- 发布记录与导出

@router.get("/campaigns/{campaign_id}/publications")
def api_list_publications(campaign_id: str):
    try:
        return {"ok": True, "publications": service.list_publications(campaign_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/contents/{content_id}/publications")
def api_backfill_publication(content_id: str, payload: PublicationInput):
    try:
        return {"ok": True, "publication": service.backfill_publication(content_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/campaigns/{campaign_id}/export-zip")
def api_export_campaign_zip(campaign_id: str):
    try:
        path = service.export_campaign_zip(campaign_id)
        return FileResponse(path, filename=path.name,
                            media_type="application/zip")
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


# ---------------------------------------------------------------- 指标与 UTM

@router.post("/campaigns/{campaign_id}/metrics")
def api_add_metric(campaign_id: str, payload: MetricInput):
    try:
        return {"ok": True, "metric": service.add_metric(campaign_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/campaigns/{campaign_id}/metrics/import")
def api_import_metrics(campaign_id: str, payload: MetricImport):
    try:
        return service.import_metrics(campaign_id, payload)
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.get("/campaigns/{campaign_id}/metrics")
def api_metrics_summary(campaign_id: str):
    try:
        return {"ok": True, "summary": service.metrics_summary(campaign_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.post("/campaigns/{campaign_id}/tracking-links")
def api_create_tracking_link(campaign_id: str, payload: TrackingLinkInput):
    try:
        return {"ok": True, "link": service.create_tracking_link(campaign_id, payload)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None


@router.get("/campaigns/{campaign_id}/tracking-links")
def api_list_tracking_links(campaign_id: str):
    try:
        return {"ok": True, "links": service.list_tracking_links(campaign_id)}
    except service.PromotionError as exc:
        raise HTTPException(_status_for(exc), str(exc)) from None
