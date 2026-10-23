"""产品推广平台业务服务。

实现《第一版产品功能文档》的核心约束：
- 产品之间数据隔离（素材、事实、活动、内容分别归属产品）。
- 未知信息标为待补充；不把计划能力当已上线；不虚构免费承诺。
- 内容编辑产生新修订，旧审核失效；审核绑定具体修订。
- 导出只标记已导出，不视为已发布；发布记录需人工回填并注明来源。
- 指标导入按批次去重，避免重复累计。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import urllib.parse
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import db
from .schemas import (
    AssetInput, CampaignCreate, CampaignUpdate, ContentCreate, ContentEdit,
    FactInput, FactUpdate, MetricImport, MetricInput, ProductCreate,
    ProductUpdate, PublicationInput, ReviewDecision, TrackingLinkInput,
    VersionCreate,
)

PROJECT_ROOT = db.PROJECT_ROOT
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

LAUNCH_STATUSES = ("launched", "beta", "planned", "unverified")
FACT_CATEGORIES = (
    "audience", "pain_point", "use_case", "feature", "differentiator",
    "pricing", "limit", "entry", "evidence", "other",
)
ASSET_KINDS = ("brand", "screenshot", "recording", "evidence", "document")
CAMPAIGN_TEMPLATES = {
    "launch": "首次上线",
    "feature_update": "功能更新",
    "tutorial": "场景教程",
}
CONTENT_STATUS = ("draft", "generating", "ready", "reviewed", "exported", "published")
JOB_STATUS = ("queued", "running", "succeeded", "failed", "cancelled")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_dict(row: sqlite3.Row, *, json_fields: Iterable[str] = ()) -> dict:
    data = dict(row)
    for field in json_fields:
        if field in data and isinstance(data[field], str):
            try:
                data[field] = json.loads(data[field])
            except json.JSONDecodeError:
                data[field] = [] if field.endswith(("refs", "steps", "artifacts", "angles", "tasks", "channels")) else {}
    if "archived" in data:
        data["archived"] = bool(data["archived"])
    if "confirmed" in data:
        data["confirmed"] = bool(data["confirmed"])
    if "approved" in data:
        data["approved"] = bool(data["approved"])
    return data


class PromotionError(ValueError):
    """业务校验错误（路由层转 400/404）。"""


def _get(conn, table: str, row_id: str, *, where: str = "id = ?") -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE {where}", (row_id,)).fetchone()
    if row is None:
        raise PromotionError(f"{table} 不存在：{row_id}")
    return row


# ---------------------------------------------------------------- 产品

def list_products() -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM products ORDER BY archived, updated_at DESC"
    ).fetchall()
    result = []
    for row in rows:
        item = _row_to_dict(row, json_fields=("forbidden_claims_json",))
        pid = item["id"]
        facts_confirmed = conn.execute(
            "SELECT COUNT(*) FROM product_facts WHERE product_id=? AND confirmed=1",
            (pid,)).fetchone()[0]
        facts_total = conn.execute(
            "SELECT COUNT(*) FROM product_facts WHERE product_id=?", (pid,)).fetchone()[0]
        assets_total = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE product_id=?", (pid,)).fetchone()[0]
        campaigns_total = conn.execute(
            "SELECT COUNT(*) FROM campaigns WHERE product_id=?", (pid,)).fetchone()[0]
        item.update(
            facts_confirmed=facts_confirmed, facts_total=facts_total,
            assets_total=assets_total, campaigns_total=campaigns_total,
        )
        result.append(item)
    return result


def get_product(product_id: str) -> dict:
    conn = db.connect()
    product = _row_to_dict(_get(conn, "products", product_id))
    versions = conn.execute(
        "SELECT * FROM product_versions WHERE product_id=? ORDER BY created_at DESC",
        (product_id,)).fetchall()
    facts = conn.execute(
        "SELECT * FROM product_facts WHERE product_id=? ORDER BY created_at",
        (product_id,)).fetchall()
    assets = conn.execute(
        "SELECT * FROM assets WHERE product_id=? ORDER BY created_at DESC",
        (product_id,)).fetchall()
    campaigns = conn.execute(
        "SELECT * FROM campaigns WHERE product_id=? ORDER BY updated_at DESC",
        (product_id,)).fetchall()
    product["versions"] = [_row_to_dict(v, json_fields=("fact_snapshot",)) for v in versions]
    product["facts"] = [_row_to_dict(f) for f in facts]
    product["assets"] = [_row_to_dict(a) for a in assets]
    product["campaigns"] = [_row_to_dict(c, json_fields=("channels", "angles", "tasks")) for c in campaigns]
    return product


def create_product(payload: ProductCreate) -> dict:
    conn = db.connect()
    ts = now()
    pid = new_id("prd")
    conn.execute(
        "INSERT INTO products (id, name, tagline, website, language, logo_path,"
        " brand_color, default_persona, forbidden_claims, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (pid, payload.name.strip(), payload.tagline.strip(), payload.website.strip(),
         payload.language, payload.logo_path.strip(), payload.brand_color.strip(),
         payload.default_persona.strip(), payload.forbidden_claims.strip(), ts, ts),
    )
    conn.commit()
    return get_product(pid)


def update_product(product_id: str, payload: ProductUpdate) -> dict:
    conn = db.connect()
    _get(conn, "products", product_id)
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return get_product(product_id)
    if "archived" in fields:
        fields["archived"] = 1 if fields["archived"] else 0
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE products SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), now(), product_id),
    )
    conn.commit()
    return get_product(product_id)


# ---------------------------------------------------------------- 事实与版本

def add_fact(product_id: str, payload: FactInput) -> dict:
    conn = db.connect()
    _get(conn, "products", product_id)
    if payload.launch_status not in LAUNCH_STATUSES:
        raise PromotionError(f"launch_status 必须是 {LAUNCH_STATUSES} 之一")
    if payload.category not in FACT_CATEGORIES:
        raise PromotionError(f"category 必须是 {FACT_CATEGORIES} 之一")
    fid = new_id("fact")
    conn.execute(
        "INSERT INTO product_facts (id, product_id, version_id, category, content,"
        " source, launch_status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (fid, product_id, None, payload.category, payload.content.strip(),
         payload.source, payload.launch_status, now()),
    )
    conn.commit()
    return _row_to_dict(_get(conn, "product_facts", fid))


def update_fact(product_id: str, fact_id: str, payload: FactUpdate) -> dict:
    conn = db.connect()
    row = _get(conn, "product_facts", fact_id)
    if row["product_id"] != product_id:
        raise PromotionError("事实不属于该产品")
    fields = payload.model_dump(exclude_none=True)
    if "confirmed" in fields:
        fields["confirmed"] = 1 if fields["confirmed"] else 0
        fields["confirmed_at"] = now() if fields["confirmed"] else None
    if "launch_status" in fields and fields["launch_status"] not in LAUNCH_STATUSES:
        raise PromotionError(f"launch_status 必须是 {LAUNCH_STATUSES} 之一")
    if not fields:
        return _row_to_dict(row)
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE product_facts SET {sets} WHERE id = ?",
        (*fields.values(), fact_id),
    )
    conn.commit()
    return _row_to_dict(_get(conn, "product_facts", fact_id))


def _snapshot_facts(conn, product_id: str) -> str:
    rows = conn.execute(
        "SELECT category, content, source, launch_status, confirmed FROM product_facts"
        " WHERE product_id=? ORDER BY created_at", (product_id,)).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


def create_version(product_id: str, payload: VersionCreate) -> dict:
    conn = db.connect()
    _get(conn, "products", product_id)
    vid = new_id("ver")
    conn.execute(
        "INSERT INTO product_versions (id, product_id, version_label, fact_snapshot,"
        " created_at) VALUES (?,?,?,?,?)",
        (vid, product_id, payload.version_label.strip(), _snapshot_facts(conn, product_id), now()),
    )
    conn.commit()
    return _row_to_dict(_get(conn, "product_versions", vid), json_fields=("fact_snapshot",))


def confirm_version(product_id: str, version_id: str) -> dict:
    """确认版本档案快照：未确认事实标为待补充并保留，不推断为承诺。"""
    conn = db.connect()
    row = _get(conn, "product_versions", version_id)
    if row["product_id"] != product_id:
        raise PromotionError("版本不属于该产品")
    conn.execute(
        "UPDATE product_versions SET confirmed=1, confirmed_at=? WHERE id=?",
        (now(), version_id),
    )
    conn.execute(
        "UPDATE product_facts SET confirmed=1, confirmed_at=? WHERE product_id=? AND confirmed=0",
        (now(), product_id),
    )
    conn.commit()
    # 产品事实更新后，引用旧事实的已审核内容标为待复核（审核失效）
    _invalidate_stale_reviews(conn, product_id)
    return _row_to_dict(_get(conn, "product_versions", version_id), json_fields=("fact_snapshot",))


def _invalidate_stale_reviews(conn, product_id: str) -> None:
    """产品事实变化后，该产品下所有内容的审核标记失效（待复核）。"""
    ts = now()
    conn.execute(
        """
        UPDATE reviews SET invalidated_at = ?
        WHERE invalidated_at IS NULL AND approved = 1 AND content_id IN (
            SELECT cv.id FROM content_variants cv
            JOIN campaigns c ON c.id = cv.campaign_id
            WHERE c.product_id = ?
        )
        """,
        (ts, product_id),
    )
    conn.execute(
        """
        UPDATE content_variants SET status = 'ready'
        WHERE status = 'reviewed' AND campaign_id IN (
            SELECT id FROM campaigns WHERE product_id = ?
        )
        """,
        (product_id,),
    )


# ---------------------------------------------------------------- 素材

def add_asset(product_id: str, payload: AssetInput) -> dict:
    conn = db.connect()
    _get(conn, "products", product_id)
    if payload.kind not in ASSET_KINDS:
        raise PromotionError(f"kind 必须是 {ASSET_KINDS} 之一")
    path = payload.path.strip().replace("\\", "/")
    resolved = (OUTPUTS_DIR / path).resolve()
    if OUTPUTS_DIR.resolve() not in resolved.parents:
        raise PromotionError("素材路径必须位于内容库 outputs/ 内")
    if not resolved.is_file():
        raise PromotionError(f"素材文件不存在：{path}")
    aid = new_id("ast")
    conn.execute(
        "INSERT INTO assets (id, product_id, version_id, kind, name, path, note,"
        " source, restrictions, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (aid, product_id, payload.version_id, payload.kind, payload.name.strip(),
         path, payload.note.strip(), payload.source, payload.restrictions.strip(), now()),
    )
    conn.commit()
    return _row_to_dict(_get(conn, "assets", aid))


def list_assets(product_id: str) -> list[dict]:
    conn = db.connect()
    _get(conn, "products", product_id)
    rows = conn.execute(
        "SELECT * FROM assets WHERE product_id=? ORDER BY created_at DESC",
        (product_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_asset(product_id: str, asset_id: str) -> dict:
    """删除被内容引用的素材时阻止，避免破坏已审核内容。"""
    conn = db.connect()
    row = _get(conn, "assets", asset_id)
    if row["product_id"] != product_id:
        raise PromotionError("素材不属于该产品")
    referencing = []
    for revision in conn.execute(
        "SELECT cr.id, cr.asset_refs, cv.id AS content_id FROM content_revisions cr"
        " JOIN content_variants cv ON cv.id = cr.content_id"
        " JOIN campaigns c ON c.id = cv.campaign_id WHERE c.product_id = ?",
        (product_id,),
    ).fetchall():
        try:
            refs = json.loads(revision["asset_refs"] or "[]")
        except json.JSONDecodeError:
            refs = []
        if asset_id in refs:
            referencing.append(revision["content_id"])
    if referencing:
        raise PromotionError(
            f"素材被 {len(referencing)} 个内容修订引用，请先解除引用或删除相关内容")
    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    conn.commit()
    return {"ok": True, "deleted": asset_id}


# ---------------------------------------------------------------- 活动

def list_campaigns(product_id: str | None = None) -> list[dict]:
    conn = db.connect()
    if product_id:
        _get(conn, "products", product_id)
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE product_id=? ORDER BY updated_at DESC",
            (product_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM campaigns ORDER BY updated_at DESC").fetchall()
    return [_row_to_dict(r, json_fields=("channels", "angles", "tasks")) for r in rows]


def get_campaign(campaign_id: str) -> dict:
    conn = db.connect()
    campaign = _row_to_dict(
        _get(conn, "campaigns", campaign_id),
        json_fields=("channels", "angles", "tasks"),
    )
    contents = conn.execute(
        "SELECT * FROM content_variants WHERE campaign_id=? ORDER BY created_at",
        (campaign_id,)).fetchall()
    content_items = []
    for row in contents:
        item = _row_to_dict(row)
        revision = conn.execute(
            "SELECT * FROM content_revisions WHERE content_id=? ORDER BY revision_no DESC LIMIT 1",
            (row["id"],)).fetchone()
        if revision is not None:
            rev = _row_to_dict(revision, json_fields=("structure", "asset_refs", "input_snapshot"))
            review = conn.execute(
                "SELECT * FROM reviews WHERE revision_id=? AND invalidated_at IS NULL"
                " ORDER BY approved_at DESC LIMIT 1", (revision["id"],)).fetchone()
            rev["review"] = _row_to_dict(review) if review is not None else None
            item["latest_revision"] = rev
        content_items.append(item)
    campaign["contents"] = content_items
    jobs = conn.execute(
        "SELECT * FROM generation_jobs WHERE campaign_id=? ORDER BY created_at DESC LIMIT 50",
        (campaign_id,)).fetchall()
    campaign["jobs"] = [_row_to_dict(j, json_fields=("params", "steps", "artifacts")) for j in jobs]
    campaign["publications"] = list_publications(campaign_id)
    campaign["metrics_summary"] = metrics_summary(campaign_id)
    return campaign


def create_campaign(payload: CampaignCreate) -> dict:
    conn = db.connect()
    _get(conn, "products", payload.product_id)
    if payload.template not in CAMPAIGN_TEMPLATES:
        raise PromotionError(f"template 必须是 {list(CAMPAIGN_TEMPLATES)} 之一")
    cid = new_id("cmp")
    ts = now()
    conn.execute(
        "INSERT INTO campaigns (id, product_id, version_id, template, goal, audience,"
        " channels, voice, cta, plan_date, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, payload.product_id, payload.version_id, payload.template, payload.goal,
         payload.audience.strip(), json.dumps(payload.channels), payload.voice.strip(),
         payload.cta.strip(), payload.plan_date, ts, ts),
    )
    conn.commit()
    return get_campaign(cid)


def update_campaign(campaign_id: str, payload: CampaignUpdate) -> dict:
    conn = db.connect()
    _get(conn, "campaigns", campaign_id)
    fields = payload.model_dump(exclude_none=True)
    for key in ("channels", "angles", "tasks"):
        if key in fields:
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    if "brief_confirmed" in fields:
        fields["brief_confirmed"] = 1 if fields["brief_confirmed"] else 0
    if "status" in fields:
        raise PromotionError("status 由系统按任务与审核状态流转，不直接修改")
    if not fields:
        return get_campaign(campaign_id)
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE campaigns SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), now(), campaign_id),
    )
    conn.commit()
    return get_campaign(campaign_id)


# ---------------------------------------------------------------- 内容与修订

def list_contents(campaign_id: str) -> list[dict]:
    conn = db.connect()
    _get(conn, "campaigns", campaign_id)
    rows = conn.execute(
        "SELECT * FROM content_variants WHERE campaign_id=? ORDER BY created_at",
        (campaign_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def create_content(payload: ContentCreate) -> dict:
    conn = db.connect()
    _get(conn, "campaigns", payload.campaign_id)
    cid = new_id("cnt")
    ts = now()
    conn.execute(
        "INSERT INTO content_variants (id, campaign_id, platform, format, angle,"
        " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cid, payload.campaign_id, payload.platform, payload.format,
         payload.angle, "draft", ts, ts),
    )
    conn.execute(
        "INSERT INTO content_revisions (id, content_id, revision_no, title, body,"
        " created_at) VALUES (?,?,1,?,?,?)",
        (new_id("rev"), cid, payload.title, payload.body, ts),
    )
    conn.execute("UPDATE content_variants SET current_revision=1 WHERE id=?", (cid,))
    conn.commit()
    return get_content(cid)


def get_content(content_id: str) -> dict:
    conn = db.connect()
    content = _row_to_dict(_get(conn, "content_variants", content_id))
    revisions = conn.execute(
        "SELECT * FROM content_revisions WHERE content_id=? ORDER BY revision_no",
        (content_id,)).fetchall()
    items = []
    for row in revisions:
        rev = _row_to_dict(row, json_fields=("structure", "asset_refs", "input_snapshot"))
        review = conn.execute(
            "SELECT * FROM reviews WHERE revision_id=? ORDER BY approved_at DESC LIMIT 1",
            (row["id"],)).fetchone()
        rev["review"] = _row_to_dict(review) if review is not None else None
        items.append(rev)
    content["revisions"] = items
    content["assets"] = [
        _row_to_dict(r) for r in conn.execute(
            "SELECT * FROM assets WHERE product_id ="
            " (SELECT product_id FROM campaigns WHERE id = ?)"
            " ORDER BY created_at DESC", (content["campaign_id"],)).fetchall()
    ]
    return content


def edit_content(content_id: str, payload: ContentEdit) -> dict:
    """编辑内容产生新修订；任何正文/标题/素材变化都会使旧审核失效。"""
    conn = db.connect()
    content = _get(conn, "content_variants", content_id)
    latest = conn.execute(
        "SELECT * FROM content_revisions WHERE content_id=? ORDER BY revision_no DESC LIMIT 1",
        (content_id,)).fetchone()
    if latest is None:
        raise PromotionError("内容没有基线修订")
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return get_content(content_id)
    title = fields.get("title", latest["title"])
    body = fields.get("body", latest["body"])
    structure = json.dumps(fields.get("structure") or json.loads(latest["structure"] or "{}"),
                           ensure_ascii=False)
    asset_refs = json.dumps(fields.get("asset_refs") or json.loads(latest["asset_refs"] or "[]"),
                            ensure_ascii=False)
    revision_no = latest["revision_no"] + 1
    ts = now()
    conn.execute(
        "INSERT INTO content_revisions (id, content_id, revision_no, title, body,"
        " structure, asset_refs, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (new_id("rev"), content_id, revision_no, title, body, structure, asset_refs, ts),
    )
    conn.execute(
        "UPDATE content_variants SET current_revision=?, updated_at=?,"
        " status = CASE WHEN status IN ('reviewed','exported','published') THEN 'ready' ELSE status END"
        " WHERE id=?",
        (revision_no, ts, content_id),
    )
    # 旧修订的有效审核失效（内容已变化）
    conn.execute(
        "UPDATE reviews SET invalidated_at=? WHERE revision_id=? AND invalidated_at IS NULL",
        (ts, latest["id"]),
    )
    conn.execute("UPDATE campaigns SET updated_at=? WHERE id=?",
                 (ts, content["campaign_id"]))
    conn.commit()
    return get_content(content_id)


# ---------------------------------------------------------------- 审核

def record_check(content_id: str, checks: list[dict]) -> dict:
    conn = db.connect()
    content = _get(conn, "content_variants", content_id)
    latest = conn.execute(
        "SELECT * FROM content_revisions WHERE content_id=? ORDER BY revision_no DESC LIMIT 1",
        (content_id,)).fetchone()
    rid = new_id("rev_log")
    conn.execute(
        "INSERT INTO reviews (id, revision_id, content_id, checks, created_at)"
        " VALUES (?,?,?,?,?)",
        (rid, latest["id"], content_id, json.dumps(checks, ensure_ascii=False), now()),
    )
    conn.commit()
    return get_content(content_id)


def approve_content(content_id: str, payload: ReviewDecision) -> dict:
    """人工确认绑定当前修订；存在未通过的检查项时不允许通过。"""
    conn = db.connect()
    content = _get(conn, "content_variants", content_id)
    latest = conn.execute(
        "SELECT * FROM content_revisions WHERE content_id=? ORDER BY revision_no DESC LIMIT 1",
        (content_id,)).fetchone()
    if not payload.approved:
        conn.execute(
            "UPDATE reviews SET invalidated_at=? WHERE revision_id=? AND invalidated_at IS NULL",
            (now(), latest["id"]),
        )
        conn.execute(
            "UPDATE content_variants SET status='ready', updated_at=? WHERE id=?",
            (now(), content_id),
        )
        conn.commit()
        return get_content(content_id)
    last_review = conn.execute(
        "SELECT * FROM reviews WHERE revision_id=? ORDER BY created_at DESC LIMIT 1",
        (latest["id"],)).fetchone()
    checks = []
    if last_review is not None:
        try:
            checks = json.loads(last_review["checks"] or "[]")
        except json.JSONDecodeError:
            checks = []
    blocking = [c for c in checks if c.get("severity") == "error"]
    if blocking:
        raise PromotionError(f"存在 {len(blocking)} 个未通过的检查项，处理后再审核")
    if last_review is not None:
        conn.execute(
            "UPDATE reviews SET approved=1, approved_by=?, approved_at=?,"
            " invalidated_at=NULL WHERE id=?",
            (payload.approved_by, now(), last_review["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO reviews (id, revision_id, content_id, checks, approved,"
            " approved_by, approved_at, created_at) VALUES (?,?,?,?,1,?,?,?)",
            (new_id("rev_log"), latest["id"], content_id, "[]",
             payload.approved_by, now(), now()),
        )
    conn.execute(
        "UPDATE content_variants SET status='reviewed', updated_at=? WHERE id=?",
        (now(), content_id),
    )
    conn.execute("UPDATE campaigns SET updated_at=? WHERE id=?",
                 (now(), content["campaign_id"]))
    conn.commit()
    return get_content(content_id)


# ---------------------------------------------------------------- 发布记录

def list_publications(campaign_id: str) -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM publications WHERE campaign_id=? ORDER BY created_at DESC",
        (campaign_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def backfill_publication(content_id: str, payload: PublicationInput) -> dict:
    """人工回填发布记录：注明人工确认、未经平台自动核验。"""
    conn = db.connect()
    content = _get(conn, "content_variants", content_id)
    latest = conn.execute(
        "SELECT id FROM content_revisions WHERE content_id=? ORDER BY revision_no DESC LIMIT 1",
        (content_id,)).fetchone()
    pub_id = new_id("pub")
    conn.execute(
        "INSERT INTO publications (id, content_id, revision_id, campaign_id, channel,"
        " account, published_at, link, status, confirm_source, note, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (pub_id, content_id, latest["id"], content["campaign_id"], payload.channel,
         payload.account, payload.published_at or now(), payload.link.strip(),
         payload.status, "manual-confirmation", payload.note.strip(), now()),
    )
    if payload.status == "published":
        conn.execute(
            "UPDATE content_variants SET status='published', updated_at=? WHERE id=?",
            (now(), content_id))
    conn.commit()
    return _row_to_dict(_get(conn, "publications", pub_id))


# ---------------------------------------------------------------- 指标

def add_metric(campaign_id: str, payload: MetricInput) -> dict:
    conn = db.connect()
    _get(conn, "campaigns", campaign_id)
    mid = new_id("met")
    conn.execute(
        "INSERT INTO metric_records (id, campaign_id, content_id, platform, metric,"
        " value, unit, source, window_start, window_end, import_batch, recorded_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, campaign_id, payload.content_id, payload.platform, payload.metric,
         payload.value, payload.unit, "manual", payload.window_start,
         payload.window_end, "", now()),
    )
    conn.commit()
    return _row_to_dict(_get(conn, "metric_records", mid))


def import_metrics(campaign_id: str, payload: MetricImport) -> dict:
    """按批次导入：重复批次直接拒绝，避免重复累计同一时间窗数据。"""
    conn = db.connect()
    _get(conn, "campaigns", campaign_id)
    exists = conn.execute(
        "SELECT COUNT(*) FROM metric_records WHERE campaign_id=? AND import_batch=?",
        (campaign_id, payload.batch)).fetchone()[0]
    if exists:
        raise PromotionError(f"导入批次 {payload.batch} 已存在（{exists} 条），拒绝重复导入")
    inserted = []
    for row in payload.rows:
        mid = new_id("met")
        conn.execute(
            "INSERT INTO metric_records (id, campaign_id, content_id, platform, metric,"
            " value, unit, source, window_start, window_end, import_batch, recorded_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, campaign_id, row.content_id, row.platform, row.metric, row.value,
             row.unit, "import", row.window_start, row.window_end, payload.batch, now()),
        )
        inserted.append(mid)
    conn.commit()
    return {"ok": True, "batch": payload.batch, "imported": len(inserted)}


def metrics_summary(campaign_id: str) -> dict:
    """按指标聚合；区分人工/导入，标注时间窗，不跨时间窗累加。"""
    conn = db.connect()
    rows = conn.execute(
        "SELECT metric, platform, source, window_start, window_end, COUNT(*) AS n,"
        " SUM(value) AS total FROM metric_records WHERE campaign_id=?"
        " GROUP BY metric, platform, source, window_start, window_end"
        " ORDER BY metric, window_end DESC",
        (campaign_id,)).fetchall()
    by_metric: dict[str, list[dict]] = {}
    for row in rows:
        by_metric.setdefault(row["metric"], []).append(dict(row))
    return {"by_metric": by_metric}


# ---------------------------------------------------------------- UTM 链接

def create_tracking_link(campaign_id: str, payload: TrackingLinkInput) -> dict:
    conn = db.connect()
    campaign = _get(conn, "campaigns", campaign_id)
    utm = {
        "utm_source": payload.utm_source or campaign["template"],
        "utm_medium": payload.utm_medium,
        "utm_campaign": campaign_id,
    }
    if payload.content_id:
        utm["utm_content"] = payload.content_id
    query = urllib.parse.urlencode(utm)
    separator = "&" if "?" in payload.target_url else "?"
    full = f"{payload.target_url}{separator}{query}"
    tid = new_id("utm")
    conn.execute(
        "INSERT INTO tracking_links (id, campaign_id, content_id, target_url, utm,"
        " full_url, created_at) VALUES (?,?,?,?,?,?,?)",
        (tid, campaign_id, payload.content_id, payload.target_url,
         json.dumps(utm, ensure_ascii=False), full, now()),
    )
    conn.commit()
    return _row_to_dict(_get(conn, "tracking_links", tid), json_fields=("utm",))


def list_tracking_links(campaign_id: str) -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM tracking_links WHERE campaign_id=? ORDER BY created_at DESC",
        (campaign_id,)).fetchall()
    return [_row_to_dict(r, json_fields=("utm",)) for r in rows]


# ---------------------------------------------------------------- 活动导出 ZIP

def export_campaign_zip(campaign_id: str) -> Path:
    """按活动导出 ZIP：按渠道分类，附发布说明；只导出已审核内容。"""
    conn = db.connect()
    campaign = get_campaign(campaign_id)
    destination = OUTPUTS_DIR / "_promotion" / "exports"
    destination.mkdir(parents=True, exist_ok=True)
    zip_path = destination / f"campaign-{campaign_id}-{uuid.uuid4().hex[:8]}.zip"
    exported = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        notes = [f"# 宣传包：{campaign_id}",
                 f"模板：{CAMPAIGN_TEMPLATES.get(campaign['template'], campaign['template'])}",
                 f"导出时间：{now()}", ""]
        for content in campaign["contents"]:
            revision = content.get("latest_revision")
            if revision is None or content["status"] not in ("reviewed", "exported", "published"):
                continue
            review = revision.get("review") or {}
            if not review.get("approved"):
                continue
            channel_dir = content["platform"]
            base = f"{channel_dir}/{content['format']}-{content['id']}"
            zf.writestr(f"{base}.md",
                        f"# {revision['title']}\n\n{revision['body']}\n")
            structure = revision.get("structure") or {}
            if structure:
                zf.writestr(f"{base}.structure.json",
                            json.dumps(structure, ensure_ascii=False, indent=2))
            for asset_id in revision.get("asset_refs", []):
                asset = conn.execute(
                    "SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
                if asset is None:
                    continue
                source = (OUTPUTS_DIR / asset["path"]).resolve()
                if source.is_file():
                    zf.write(source, f"{channel_dir}/assets/{source.name}")
            exported += 1
            notes.append(
                f"- {channel_dir}/{content['format']}：{revision['title']}"
                f"（修订 {revision['revision_no']}，已人工审核）")
        notes += ["", "## 发布说明", "",
                  "- 导出仅代表内容已生成并经人工审核，不代表已发布。",
                  "- 请在对应官方平台登录后提交，并在活动页回填作品链接与发布时间。"]
        zf.writestr("发布说明.md", "\n".join(notes))
    if exported == 0:
        zip_path.unlink(missing_ok=True)
        raise PromotionError("没有可导出的内容：需要至少一条已人工审核的内容")
    return zip_path
