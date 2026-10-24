"""产品推广平台（easel.promotion）核心流程与约束测试。

覆盖《第一版产品功能文档》第 6 节验收场景中可自动化的部分：
多产品隔离、缺失信息不编造、计划功能口径、审核失效、
指标批次去重、导出不标记已发布、任务重启恢复。
"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

from easel.promotion import checks, db, generation, jobs, service
from easel.promotion.schemas import (
    AssetInput, CampaignCreate, CampaignUpdate, ContentCreate, ContentEdit,
    FactInput, FactUpdate, MetricImport, MetricInput, ProductCreate,
    PublicationInput, ReviewDecision, TrackingLinkInput, VersionCreate,
    MetricImportRow,
)


@pytest.fixture()
def promo_db(tmp_path: Path, monkeypatch):
    """每个测试独立数据库，避免与真实 outputs 下的库串写。"""
    monkeypatch.setenv("EASEL_PROMOTION_DB", str(tmp_path / "promotion.db"))
    monkeypatch.setattr(service, "OUTPUTS_DIR", tmp_path / "outputs")
    service.OUTPUTS_DIR.mkdir()
    monkeypatch.setattr(generation, "_llm_json", lambda *_a, **_kw: None)
    db.reset_thread_connection()
    db.init_db(tmp_path / "promotion.db")
    yield db
    db.reset_thread_connection()


def _product(name: str = "Showly", **kw) -> dict:
    return service.create_product(ProductCreate(name=name, **kw))


def _fact(pid: str, content: str, category: str = "feature",
          status: str = "launched") -> dict:
    fact = service.add_fact(pid, FactInput(
        category=category, content=content, launch_status=status))
    return service.update_fact(pid, fact["id"], FactUpdate(confirmed=True))


def _campaign(pid: str, template: str = "launch") -> dict:
    version = service.create_version(pid, VersionCreate(version_label="v1"))
    service.confirm_version(pid, version["id"])
    campaign = service.create_campaign(CampaignCreate(
        product_id=pid, template=template, version_id=version["id"], channels=["xiaohongshu", "wechat-oa"]))
    return service.update_campaign(campaign["id"], CampaignUpdate(brief_confirmed=True))


def _wait_job(job_id: str) -> dict:
    for _ in range(200):
        job = jobs.get_job(job_id)
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("任务超时未完成")


def _content(cid: str, platform: str = "xiaohongshu", fmt: str = "note") -> dict:
    return service.create_content(ContentCreate(
        campaign_id=cid, platform=platform, format=fmt, title="产品介绍", body="产品介绍，访问 https://example.test"))


# ---------------------------------------------------------------- 阶段一：骨架

def test_product_crud_and_archive(promo_db):
    p = _product(name="A", tagline="介绍")
    assert p["name"] == "A"
    product = service.get_product(p["id"])
    product["facts"] and pytest.fail("新产品不应有事实")
    archived = service.update_product(p["id"], __import__("easel.promotion.schemas",
                                                          fromlist=["ProductUpdate"]).ProductUpdate(archived=True))
    assert archived["archived"] is True
    listing = service.list_products()
    assert listing[0]["archived"] is True


def test_multi_product_isolation(promo_db):
    """验收：切换产品不会混用其他产品的素材和功能。"""
    pa, pb = _product("A"), _product("B")
    _fact(pa["id"], "A 的独有功能")
    campaign_b = _campaign(pb["id"])
    content_b = _content(campaign_b["id"])
    content = service.get_content(content_b["id"])
    facts_a = [f for f in content.get("facts", [])]
    assert facts_a == []
    # A 的素材不能写入 B 的内容，不能只依赖可选检查。
    asset_dir = service.OUTPUTS_DIR / "_promotion" / "assets" / pa["id"]
    asset_dir.mkdir(parents=True, exist_ok=True)
    f = asset_dir / "shot.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n")
    asset_a = service.add_asset(pa["id"], AssetInput(
        kind="screenshot", name="shot",
        path=str(f.relative_to(service.OUTPUTS_DIR)).replace("\\", "/")))
    with pytest.raises(service.PromotionError, match="素材不属于"):
        service.edit_content(content_b["id"], ContentEdit(
            title="B 内容", body="正文", asset_refs=[asset_a["id"]]))
    assert service.get_content(content_b["id"])["revisions"][-1]["asset_refs"] == []


def test_unconfirmed_pricing_no_free_claim(promo_db):
    """验收：未提供定价时不会默认生成「完全免费」，且声称免费会被检查拦下。"""
    p = _product()
    _fact(p["id"], "功能一")
    campaign = _campaign(p["id"])
    draft = generation.generate_content(_content(campaign["id"])["id"])
    assert "完全免费" not in draft["body"]
    # 正文声称免费 -> 检查报 error
    service.edit_content(_content_of(campaign), ContentEdit(
        title="标题", body="产品完全免费，快来用"))
    content = service.get_content(_content_of(campaign))
    issues = checks.run_checks(content, content["revisions"][-1])
    assert any(i["item"] == "定价与限制" and i["severity"] == "error" for i in issues)


def _content_of(campaign: dict) -> str:
    """取活动下第一条内容；没有则建一条（大部分验收场景从空白开始）。"""
    items = service.list_contents(campaign["id"])
    if not items:
        items = [_content(campaign["id"])]
    return items[0]["id"]


def test_planned_feature_not_lauunched(promo_db):
    """验收：宣传计划功能时，检查指出未上线状态。"""
    p = _product()
    _fact(p["id"], "智能批量剪辑", status="planned")
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    service.edit_content(cid, ContentEdit(
        title="新工具", body="智能批量剪辑 已上线，人人可用"))
    content = service.get_content(cid)
    issues = checks.run_checks(content, content["revisions"][-1])
    assert any(i["item"] == "功能事实" and i["severity"] == "error" for i in issues)
    # 有 error 时审核被阻止
    service.record_check(cid, issues)
    with pytest.raises(service.PromotionError):
        service.approve_content(cid, ReviewDecision(approved=True))


def test_forbidden_claims_blocked(promo_db):
    p = _product(name="X", forbidden_claims="业界第一;百分百安全")
    _fact(p["id"], "功能")
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    service.edit_content(cid, ContentEdit(title="t", body="我们是业界第一"))
    content = service.get_content(cid)
    issues = checks.run_checks(content, content["revisions"][-1])
    assert any(i["item"] == "宣传红线" for i in issues)


# ---------------------------------------------------------------- 阶段二：内容与审核

def test_review_binds_revision_and_invalidates_on_edit(promo_db):
    """验收：审核后编辑，旧审核失效。"""
    p = _product()
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    service.record_check(cid, [])
    approved = service.approve_content(cid, ReviewDecision(approved=True))
    assert approved["status"] == "reviewed"
    edited = service.edit_content(cid, ContentEdit(title="改了"))
    assert edited["status"] != "reviewed"
    old = next(r for r in edited["revisions"] if r["revision_no"] == approved["current_revision"])
    assert old["review"]["invalidated_at"] is not None


def test_fact_update_invalidates_reviews(promo_db):
    """验收：产品事实更新后，引用旧事实的已审核内容标为待复核。"""
    p = _product()
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    service.record_check(cid, [])
    service.approve_content(cid, ReviewDecision(approved=True))
    service.add_fact(p["id"], FactInput(category="limit", content="新增适用限制"))
    db.reset_thread_connection()
    assert service.get_content(cid)["status"] == "ready"


def test_generation_job_and_restart_recovery(promo_db):
    """验收：制作任务失败/重启后，已完成内容保留，未完成任务可识别。"""
    p = _product()
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    job = jobs.create_job("content_draft", content_id=cid)
    result = _wait_job(job["id"])
    assert result["status"] == "succeeded"
    content = service.get_content(cid)
    assert content["revisions"][-1]["body"]

    # 模拟服务重启：手工把一条任务置为 running 再恢复
    import json as _json
    conn = db.connect()
    conn.execute(
        "INSERT INTO generation_jobs (id, campaign_id, job_type, status, params,"
        " created_at, updated_at) VALUES ('job_stuck', ?, 'brief', 'running', '{}', ?, ?)",
        (campaign["id"], service.now(), service.now()))
    conn.commit()
    recovered = jobs.recover_interrupted_jobs()
    assert recovered >= 1
    stuck = jobs.get_job("job_stuck")
    assert stuck["status"] == "failed"
    assert "重启" in stuck["error"]
    # 已完成任务不受影响
    assert jobs.get_job(job["id"])["status"] == "succeeded"


def test_job_idempotency_no_duplicate_running(promo_db):
    p = _product()
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    jobs.create_job("checks", content_id=cid, auto_start=False)
    with pytest.raises(service.PromotionError):
        jobs.create_job("checks", content_id=cid)


def test_template_generation_fallback_marked(promo_db):
    """未配置模型时降级为模板草稿，并明确标注。"""
    p = _product()
    campaign = _campaign(p["id"])
    result = generation.generate_brief(campaign["id"])
    assert result["generation_mode"] in ("model", "template")
    if result["generation_mode"] == "template":
        assert "模板" in result["brief"]


# ---------------------------------------------------------------- 阶段三：发布与复盘

def test_export_requires_review_and_marks_export_only(promo_db):
    """验收：导出不标记发布成功。"""
    p = _product()
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    with pytest.raises(service.PromotionError):
        service.export_campaign_zip(campaign["id"])
    service.record_check(cid, [])
    service.approve_content(cid, ReviewDecision(approved=True))
    zip_path = service.export_campaign_zip(campaign["id"])
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any("发布说明" in n for n in names)
    # 导出后内容状态不变成 published；无发布记录
    content = service.get_content(cid)
    assert content["status"] == "exported"
    assert service.list_publications(campaign["id"]) == []


def test_publication_backfill_manual_source(promo_db):
    """验收：人工回填链接，标注人工确认来源。"""
    p = _product()
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    pub = service.backfill_publication(cid, PublicationInput(
        channel="xiaohongshu", link="https://xhs.example/1"))
    assert pub["confirm_source"] == "manual-confirmation"
    assert service.get_content(cid)["status"] == "published"


def test_metric_batch_dedup(promo_db):
    """验收：重复导入指标可识别批次，避免重复累计。"""
    p = _product()
    campaign = _campaign(p["id"])
    service.add_metric(campaign["id"], MetricInput(metric="views", value=10))
    rows = [MetricImportRow(metric="views", value=5, window_start="2026-10-01",
                            window_end="2026-10-07")]
    res = service.import_metrics(campaign["id"], MetricImport(batch="b1", rows=rows))
    assert res["imported"] == 1
    with pytest.raises(service.PromotionError):
        service.import_metrics(campaign["id"], MetricImport(batch="b1", rows=rows))
    summary = service.metrics_summary(campaign["id"])
    totals = [r["total"] for r in summary["by_metric"]["views"]]
    assert sorted(totals) == [5.0, 10.0]


def test_utm_link_generation(promo_db):
    p = _product()
    campaign = _campaign(p["id"])
    link = service.create_tracking_link(campaign["id"], TrackingLinkInput(
        target_url="https://example.com/?x=1", utm_source="xiaohongshu"))
    assert "utm_source=xiaohongshu" in link["full_url"]
    assert "utm_campaign=" in link["full_url"]
    assert link["full_url"].startswith("https://example.com/?x=1&")


def test_asset_delete_protected_when_referenced(promo_db):
    """删除被内容引用的素材时阻止，避免破坏已审核内容。"""
    p = _product()
    asset_dir = service.OUTPUTS_DIR / "_promotion" / "assets" / p["id"]
    asset_dir.mkdir(parents=True, exist_ok=True)
    f = asset_dir / "rec.mp4"
    f.write_bytes(b"0000")
    asset = service.add_asset(p["id"], AssetInput(
        kind="recording", name="录屏",
        path=str(f.relative_to(service.OUTPUTS_DIR)).replace("\\", "/")))
    campaign = _campaign(p["id"])
    cid = _content_of(campaign)
    service.edit_content(cid, ContentEdit(title="t", body="b", asset_refs=[asset["id"]]))
    with pytest.raises(service.PromotionError):
        service.delete_asset(p["id"], asset["id"])
