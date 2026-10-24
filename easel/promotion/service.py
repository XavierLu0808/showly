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
                data[field] = [] if field.endswith(("refs", "steps", "artifacts", "angles", "tasks", "channels", "checks", "fact_snapshot")) else {}
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


def _validate_version(conn, product_id: str, version_id: str | None):
    if version_id is None:
        return None
    version = _get(conn, "product_versions", version_id)
    if version["product_id"] != product_id:
        raise PromotionError("版本不属于该产品")
    return version


def _validate_content_owner(conn, campaign_id: str, content_id: str | None) -> None:
    if content_id is not None:
        content = _get(conn, "content_variants", content_id)
        if content["campaign_id"] != campaign_id:
            raise PromotionError("内容不属于该活动")


def _asset_path(conn, product_id: str, path: str) -> Path:
    root = OUTPUTS_DIR.resolve()
    resolved = (root / path).resolve()
    if root not in resolved.parents:
        raise PromotionError("素材路径必须位于内容库 outputs/ 内")
    relative = resolved.relative_to(root).parts
    if relative[0] == "_promotion" and (
        len(relative) < 4 or relative[1:3] != ("assets", product_id)
    ):
        raise PromotionError("素材路径不属于该产品的素材目录")
    if not resolved.is_file():
        raise PromotionError(f"素材文件不存在：{path}")
    for asset in conn.execute("SELECT product_id, path FROM assets WHERE product_id<>?", (product_id,)):
        if (root / asset["path"]).resolve() == resolved:
            raise PromotionError("素材文件已属于其他产品")
    return resolved


def _validate_asset_refs(conn, campaign, asset_refs: list[str]) -> None:
    for asset_id in asset_refs:
        asset = _get(conn, "assets", asset_id)
        if asset["product_id"] != campaign["product_id"]:
            raise PromotionError("素材不属于该产品")
        _validate_version(conn, campaign["product_id"], asset["version_id"])
        if asset["version_id"] and campaign["version_id"] and asset["version_id"] != campaign["version_id"]:
            raise PromotionError("素材版本与活动版本不一致")
        _asset_path(conn, campaign["product_id"], asset["path"])


def get_campaign_version(campaign_id: str, require_confirmed: bool = True) -> dict:
    """兼容旧活动：首次使用时绑定最新已确认版本，以后不随产品更新漂移。"""
    conn = db.connect()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        campaign = _get(conn, "campaigns", campaign_id)
        version = _validate_version(conn, campaign["product_id"], campaign["version_id"])
        if version is None:
            version = conn.execute(
                "SELECT * FROM product_versions WHERE product_id=? AND confirmed=1 ORDER BY rowid DESC LIMIT 1",
                (campaign["product_id"],),
            ).fetchone()
            if version is None:
                raise PromotionError("请先确认产品版本档案")
            conn.execute("UPDATE campaigns SET version_id=? WHERE id=?", (version["id"], campaign_id))
        if require_confirmed and not version["confirmed"]:
            raise PromotionError("请先确认产品版本档案")
    return _row_to_dict(version, json_fields=("fact_snapshot",))


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
    _invalidate_stale_reviews(conn, product_id)
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
    _invalidate_stale_reviews(conn, product_id)
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
    _invalidate_stale_reviews(conn, product_id)
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
    if row["confirmed"]:
        return _row_to_dict(row, json_fields=("fact_snapshot",))
    with conn:
        conn.execute(
            "UPDATE product_versions SET confirmed=1, confirmed_at=? WHERE id=?",
            (now(), version_id),
        )
        _invalidate_stale_reviews(conn, product_id)
    return _row_to_dict(_get(conn, "product_versions", version_id), json_fields=("fact_snapshot",))


def _invalidate_stale_reviews(conn, product_id: str) -> None:
    """产品事实变化后，该产品下所有内容的审核标记失效（待复核）。"""
    ts = now()
    conn.execute(
        """
        UPDATE reviews SET invalidated_at = ?
        WHERE invalidated_at IS NULL AND content_id IN (
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
        WHERE status IN ('reviewed','exported','published') AND campaign_id IN (
            SELECT id FROM campaigns WHERE product_id = ?
        )
        """,
        (product_id,),
    )


# ---------------------------------------------------------------- 素材

def add_asset(product_id: str, payload: AssetInput) -> dict:
    conn = db.connect()
    aid = new_id("ast")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _get(conn, "products", product_id)
        if payload.kind not in ASSET_KINDS:
            raise PromotionError(f"kind 必须是 {ASSET_KINDS} 之一")
        path = payload.path.strip().replace("\\", "/")
        _validate_version(conn, product_id, payload.version_id)
        resolved = _asset_path(conn, product_id, path)
        path = resolved.relative_to(OUTPUTS_DIR.resolve()).as_posix()
        conn.execute(
            "INSERT INTO assets (id, product_id, version_id, kind, name, path, note,"
            " source, restrictions, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, product_id, payload.version_id, payload.kind, payload.name.strip(),
             path, payload.note.strip(), payload.source, payload.restrictions.strip(), now()),
        )
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
                " ORDER BY rowid DESC LIMIT 1", (revision["id"],)).fetchone()
            rev["review"] = _row_to_dict(review, json_fields=("checks",)) if review is not None else None
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
    _validate_version(conn, payload.product_id, payload.version_id)
    version_id = payload.version_id
    if version_id is None:
        latest = conn.execute(
            "SELECT id FROM product_versions WHERE product_id=? AND confirmed=1 ORDER BY rowid DESC LIMIT 1",
            (payload.product_id,),
        ).fetchone()
        version_id = latest["id"] if latest else None
    cid = new_id("cmp")
    ts = now()
    conn.execute(
        "INSERT INTO campaigns (id, product_id, version_id, template, goal, audience,"
        " channels, voice, cta, plan_date, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, payload.product_id, version_id, payload.template, payload.goal,
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


def _validate_content_format(platform: str, fmt: str) -> None:
    allowed = {"xiaohongshu": ("note", "card_set"), "wechat-oa": ("article",), "short-video": ("video",)}
    if fmt not in allowed.get(platform, ()):
        raise PromotionError("不支持的平台或内容形式")


def _insert_content(conn, payload: ContentCreate) -> str:
    cid = new_id("cnt")
    ts = now()
    conn.execute(
        "INSERT INTO content_variants (id, campaign_id, platform, format, angle, status, created_at, updated_at, current_revision)"
        " VALUES (?,?,?,?,?,'draft',?,?,1)",
        (cid, payload.campaign_id, payload.platform, payload.format, payload.angle, ts, ts),
    )
    conn.execute(
        "INSERT INTO content_revisions (id, content_id, revision_no, title, body, created_at) VALUES (?,?,1,?,?,?)",
        (new_id("rev"), cid, payload.title, payload.body, ts),
    )
    return cid


def create_content(payload: ContentCreate) -> dict:
    _validate_content_format(payload.platform, payload.format)
    conn = db.connect()
    _get(conn, "campaigns", payload.campaign_id)
    with conn:
        cid = _insert_content(conn, payload)
    return get_content(cid)


def confirm_plan(campaign_id: str) -> list[dict]:
    get_campaign_version(campaign_id)
    conn = db.connect()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        campaign = _get(conn, "campaigns", campaign_id)
        angles = json.loads(campaign["angles"])
        selected = []
        platforms = {"note": "xiaohongshu", "card_set": "xiaohongshu", "article": "wechat-oa", "video": "short-video"}
        for angle in angles:
            if angle.get("selected", True):
                for fmt in angle.get("formats") or []:
                    if fmt not in platforms:
                        raise PromotionError(f"不支持的内容形式：{fmt}")
                    selected.append((platforms[fmt], fmt, str(angle.get("angle", ""))))
        if not selected:
            raise PromotionError("请先生成并选择宣传角度与内容形式")
        existing = {(row["platform"], row["format"], row["angle"]) for row in conn.execute(
            "SELECT platform, format, angle FROM content_variants WHERE campaign_id=?", (campaign_id,),
        )}
        created = []
        for platform, fmt, angle in selected:
            key = (platform, fmt, angle)
            if key not in existing:
                created.append(_insert_content(conn, ContentCreate(campaign_id=campaign_id, platform=platform, format=fmt, angle=angle)))
                existing.add(key)
        conn.execute("UPDATE campaigns SET brief_confirmed=1, updated_at=? WHERE id=?", (now(), campaign_id))
    return [get_content(content_id) for content_id in created]


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
            "SELECT * FROM reviews WHERE revision_id=? ORDER BY rowid DESC LIMIT 1",
            (row["id"],)).fetchone()
        rev["review"] = _row_to_dict(review, json_fields=("checks",)) if review is not None else None
        items.append(rev)
    content["revisions"] = items
    content["assets"] = [
        _row_to_dict(r) for r in conn.execute(
            "SELECT * FROM assets WHERE product_id ="
            " (SELECT product_id FROM campaigns WHERE id = ?)"
            " ORDER BY created_at DESC", (content["campaign_id"],)).fetchall()
    ]
    return content


def edit_content(content_id: str, payload: ContentEdit, *, input_snapshot: dict | None = None,
                 expected_revision_id: str | None = None) -> dict:
    """保存修订和审核失效为同一事务；后台生成不能覆盖之后的用户编辑。"""
    conn = db.connect()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        content = _get(conn, "content_variants", content_id)
        campaign = _get(conn, "campaigns", content["campaign_id"])
        latest = conn.execute(
            "SELECT * FROM content_revisions WHERE content_id=? ORDER BY revision_no DESC LIMIT 1",
            (content_id,),
        ).fetchone()
        if latest is None:
            raise PromotionError("内容没有基线修订")
        if expected_revision_id is not None and latest["id"] != expected_revision_id:
            raise PromotionError("内容修订已变化，请基于最新修订重新操作")
        fields = payload.model_dump(exclude_none=True)
        if fields or input_snapshot is not None:
            refs = fields.get("asset_refs", json.loads(latest["asset_refs"]))
            _validate_asset_refs(conn, campaign, refs)
            snapshot = input_snapshot if input_snapshot is not None else json.loads(latest["input_snapshot"])
            if input_snapshot is not None:
                context = snapshot.get("context", {})
                if context.get("product", {}).get("id") != campaign["product_id"]:
                    raise PromotionError("生成快照不属于该产品")
                version = context.get("version", {})
                _validate_version(conn, campaign["product_id"], version.get("id"))
                if version.get("id") != campaign["version_id"]:
                    raise PromotionError("生成快照与活动版本不一致")
            revision_no = latest["revision_no"] + 1
            ts = now()
            conn.execute(
                "INSERT INTO content_revisions (id, content_id, revision_no, title, body,"
                " structure, asset_refs, input_snapshot, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id("rev"), content_id, revision_no, fields.get("title", latest["title"]),
                 fields.get("body", latest["body"]),
                 json.dumps(fields.get("structure", json.loads(latest["structure"])), ensure_ascii=False),
                 json.dumps(refs, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False), ts),
            )
            conn.execute(
                "UPDATE content_variants SET current_revision=?, updated_at=?, status='ready' WHERE id=?",
                (revision_no, ts, content_id),
            )
            conn.execute(
                "UPDATE reviews SET invalidated_at=? WHERE content_id=? AND invalidated_at IS NULL",
                (ts, content_id),
            )
            conn.execute("UPDATE campaigns SET updated_at=? WHERE id=?", (ts, content["campaign_id"]))
    return get_content(content_id)


# ---------------------------------------------------------------- 审核

def _latest_revision(conn, content_id: str, revision_id: str | None = None):
    _get(conn, "content_variants", content_id)
    latest = conn.execute(
        "SELECT * FROM content_revisions WHERE content_id=? ORDER BY revision_no DESC LIMIT 1", (content_id,),
    ).fetchone()
    if latest is None:
        raise PromotionError("内容没有基线修订")
    if revision_id is not None and latest["id"] != revision_id:
        raise PromotionError("内容修订已变化，请重新检查或审核")
    return latest


def _store_check(conn, content_id: str, revision_id: str, checks: list[dict], *,
                 approved: bool = False, approved_by: str = "") -> None:
    ts = now()
    conn.execute("UPDATE reviews SET invalidated_at=? WHERE content_id=? AND invalidated_at IS NULL", (ts, content_id))
    conn.execute(
        "INSERT INTO reviews (id, revision_id, content_id, checks, approved, approved_by, approved_at, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (new_id("rev_log"), revision_id, content_id, json.dumps(checks, ensure_ascii=False),
         int(approved), approved_by, ts if approved else None, ts),
    )
    conn.execute("UPDATE content_variants SET status=?, updated_at=? WHERE id=?",
                 ("reviewed" if approved else "ready", ts, content_id))


def record_check(content_id: str, checks: list[dict], *, revision_id: str | None = None) -> dict:
    conn = db.connect()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = _latest_revision(conn, content_id, revision_id)
        _store_check(conn, content_id, latest["id"], checks)
    return get_content(content_id)


def approve_content(content_id: str, payload: ReviewDecision) -> dict:
    """在锁定的当前修订上重新检查，不能用空检查或过期审核绕过规则。"""
    from .checks import run_checks

    conn = db.connect()
    blocking = []
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = _latest_revision(conn, content_id, payload.revision_id)
        if not payload.approved:
            conn.execute("UPDATE reviews SET invalidated_at=? WHERE content_id=? AND invalidated_at IS NULL", (now(), content_id))
            conn.execute("UPDATE content_variants SET status='ready', updated_at=? WHERE id=?", (now(), content_id))
        else:
            content = get_content(content_id)
            revision = content["revisions"][-1]
            issues = run_checks(content, revision)
            blocking = [issue for issue in issues if issue.get("severity") == "error"]
            _store_check(conn, content_id, latest["id"], issues,
                         approved=not blocking, approved_by=payload.approved_by)
        conn.execute("UPDATE campaigns SET updated_at=? WHERE id=(SELECT campaign_id FROM content_variants WHERE id=?)",
                     (now(), content_id))
    if blocking:
        raise PromotionError(f"存在 {len(blocking)} 个未通过的检查项，处理后再审核")
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
    _validate_content_owner(conn, campaign_id, payload.content_id)
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
    for row in payload.rows:
        _validate_content_owner(conn, campaign_id, row.content_id)
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
    _validate_content_owner(conn, campaign_id, payload.content_id)
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
    """锁定修订并复核归属后打包；成功仅标记导出，失败清理不完整文件。"""
    from .checks import run_checks

    conn = db.connect()
    zip_path = None
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            campaign = get_campaign(campaign_id)
            _validate_version(conn, campaign["product_id"], campaign["version_id"])
            selected = []
            for content in campaign["contents"]:
                revision = content.get("latest_revision")
                review = (revision or {}).get("review") or {}
                if content["status"] not in ("reviewed", "exported", "published") or not review.get("approved"):
                    continue
                if review.get("invalidated_at") or review.get("revision_id") != revision["id"]:
                    continue
                _validate_content_format(content["platform"], content["format"])
                _validate_asset_refs(conn, campaign, revision["asset_refs"])
                if any(issue["severity"] == "error" for issue in run_checks(content, revision)):
                    raise PromotionError("已审核内容检查未通过，请重新检查与审核后导出")
                selected.append((content, revision))
            if not selected:
                raise PromotionError("没有可导出的内容：需要至少一条已人工审核的内容")
            destination = OUTPUTS_DIR / "_promotion" / "exports"
            destination.mkdir(parents=True, exist_ok=True)
            zip_path = destination / f"campaign-{campaign_id}-{uuid.uuid4().hex[:8]}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                notes = [f"# 宣传包：{campaign_id}",
                         f"模板：{CAMPAIGN_TEMPLATES.get(campaign['template'], campaign['template'])}",
                         f"导出时间：{now()}", ""]
                copied = set()
                for content, revision in selected:
                    base = f"{content['platform']}/{content['format']}-{content['id']}"
                    zf.writestr(f"{base}.md", f"# {revision['title']}\n\n{revision['body']}\n")
                    if revision["structure"]:
                        zf.writestr(f"{base}.structure.json", json.dumps(revision["structure"], ensure_ascii=False, indent=2))
                    for asset_id in revision["asset_refs"]:
                        asset = _get(conn, "assets", asset_id)
                        source = _asset_path(conn, campaign["product_id"], asset["path"])
                        entry = f"{content['platform']}/assets/{asset_id}-{source.name}"
                        if entry not in copied:
                            zf.write(source, entry)
                            copied.add(entry)
                    notes.append(f"- {content['platform']}/{content['format']}：{revision['title']}（修订 {revision['revision_no']}，已人工审核）")
                    conn.execute(
                        "UPDATE content_variants SET status=CASE WHEN status='published' THEN status ELSE 'exported' END, updated_at=? WHERE id=?",
                        (now(), content["id"]),
                    )
                notes += ["", "## 发布说明", "",
                          "- 导出仅代表内容已生成并经人工审核，不代表已发布。",
                          "- 请在对应官方平台登录后提交，并在活动页回填作品链接与发布时间。"]
                zf.writestr("发布说明.md", "\n".join(notes))
    except Exception:
        if zip_path is not None:
            zip_path.unlink(missing_ok=True)
        raise
    return zip_path
