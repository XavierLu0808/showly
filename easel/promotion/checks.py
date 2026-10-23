"""内容检查（功能文档 4.8）：事实、定价、依据、素材、格式、完整性。

规则可解释、结果带定位与建议；AI/规则给出问题，不替代人工判断。
severity 约定：error = 阻断审核通过；warn = 提示但不阻断。
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import db

# 平台格式规则（可配置维护；首版内置默认）
PLATFORM_RULES: dict[str, dict] = {
    "xiaohongshu": {"title_max": 20, "body_max": 1000, "label": "小红书"},
    "wechat-oa": {"title_max": 64, "body_max": 20000, "label": "公众号"},
    "short-video": {"title_max": 30, "body_max": 5000, "label": "短视频"},
}

FREE_CLAIM_RE = re.compile(r"完全免费|永久免费|一律免费|全部免费|免费试用无限")
NUMBER_CLAIM_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:%|％|倍|万|千))|(?:\d{3,}\s*\+?\s*(?:位|个|名)?\s*用户)")
ENTRY_HINT_RE = re.compile(r"(https?://|下载|入口|试用|体验|注册|申请)", re.I)
LAUNCHED_HINT_RE = re.compile(r"(已上线|已发布|现在就|马上能|直接用|开箱即用)")


def _facts(conn: sqlite3.Connection, campaign_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT pf.* FROM product_facts pf"
        " JOIN campaigns c ON c.product_id = pf.product_id"
        " WHERE c.id = ? ORDER BY pf.created_at", (campaign_id,)).fetchall()
    return [dict(r) for r in rows]


def _keyword_of(fact_content: str) -> str:
    """取事实内容中最有辨识度的短语做包含匹配（首版简单启发式）。"""
    text = fact_content.strip()
    for quoted in re.findall(r"[「“\"‘']([^」”\"’']{2,20})[」”\"’']", text):
        return quoted
    return text[:12].strip()


def run_checks(content: dict, revision: dict) -> list[dict]:
    """对内容修订执行全部检查，返回问题列表。"""
    conn = db.connect()
    checks: list[dict] = []
    title = (revision.get("title") or "").strip()
    body = (revision.get("body") or "").strip()
    facts = _facts(conn, content["campaign_id"])
    product_row = conn.execute(
        "SELECT p.name, p.website, p.forbidden_claims FROM products p"
        " JOIN campaigns c ON c.product_id = p.id WHERE c.id = ?",
        (content["campaign_id"],)).fetchone()
    product = dict(product_row) if product_row else {"name": "", "website": "", "forbidden_claims": ""}

    def add(item: str, severity: str, message: str, suggestion: str = "") -> None:
        checks.append({"item": item, "severity": severity,
                       "message": message, "suggestion": suggestion})

    # 1) 功能事实：不把计划/测试中能力写成已上线
    for fact in facts:
        if fact["launch_status"] not in ("planned", "beta"):
            continue
        keyword = _keyword_of(fact["content"])
        if keyword and keyword in (title + body):
            label = "计划中" if fact["launch_status"] == "planned" else "测试中"
            add("功能事实", "error",
                f"「{keyword}」是{label}能力，不能以已上线口吻宣传",
                f"改为「即将推出/内测中」，或等该能力上线后再宣传")

    # 2) 定价与限制：不编造免费承诺
    pricing_facts = [f for f in facts if f["category"] == "pricing"]
    if FREE_CLAIM_RE.search(body) or "免费" in title:
        confirmed_free = any(
            "免费" in f["content"] and f["confirmed"] for f in pricing_facts)
        if not confirmed_free:
            add("定价与限制", "error",
                "内容提到免费，但产品档案中没有已确认的免费定价事实",
                "补充并确认定价事实，或删除免费表述")
    mentions_pricing = any(word in body for word in ("价", "费", "订阅", "付费"))
    if pricing_facts and not mentions_pricing:
        # 有定价事实但正文完全未提：仅当内容形态是产品介绍时提示
        if content.get("format") in ("note", "article", "card_set"):
            add("定价与限制", "warn",
                "产品档案有定价信息，正文未提及适用条件",
                "视渠道习惯补充定价与适用条件，避免读者误解")

    # 3) 宣传依据：不虚构效果数字与用户评价
    for match in NUMBER_CLAIM_RE.finditer(body):
        snippet = body[max(0, match.start() - 10):match.end() + 10]
        evidence = [f for f in facts if f["category"] == "evidence"]
        if not evidence:
            add("宣传依据", "warn",
                f"出现数字表述「{snippet.strip()}」，但没有已登记的证明材料",
                "补充可授权引用的实测记录/评价，或删除具体数字")

    # 4) 素材一致性：引用的素材必须存在且属于本产品
    asset_refs = revision.get("asset_refs") or []
    for asset_id in asset_refs:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            add("素材一致性", "error", f"引用的素材不存在：{asset_id}", "重新选择素材")
            continue
        belongs = conn.execute(
            "SELECT 1 FROM assets a JOIN campaigns c ON c.product_id = a.product_id"
            " WHERE a.id = ? AND c.id = ?", (asset_id, content["campaign_id"])).fetchone()
        if belongs is None:
            add("素材一致性", "error",
                f"素材「{row['name']}」不属于本产品，禁止混用其他产品素材",
                "移除该素材，改用本产品素材库中的文件")

    # 5) 平台格式
    rules = PLATFORM_RULES.get(content["platform"])
    if rules:
        if title and len(title) > rules["title_max"]:
            add("平台格式", "error",
                f"{rules['label']}标题上限 {rules['title_max']} 字，当前 {len(title)} 字",
                "精简标题或另拟备选标题")
        if body and len(body) > rules["body_max"]:
            add("平台格式", "error",
                f"{rules['label']}正文上限 {rules['body_max']} 字，当前 {len(body)} 字",
                "按要求缩短正文（可用 AI 局部改写）")
    if content["format"] == "card_set":
        structure = revision.get("structure") or {}
        pages = structure.get("pages") or []
        if not pages:
            add("平台格式", "warn", "卡片内容还没有分页结构", "先生成分页文案再导出")
    if content["format"] == "video":
        structure = revision.get("structure") or {}
        if not (structure.get("script") or body):
            add("平台格式", "warn", "演示视频缺少解说词/脚本", "先生成或补写解说词")

    # 6) 内容完整性
    if not title:
        add("内容完整性", "error", "缺少标题", "填写标题")
    if not body:
        add("内容完整性", "error", "缺少正文/脚本", "先生成或填写内容")
    if product["name"] and product["name"] not in (title + body):
        add("内容完整性", "warn",
            f"正文未出现产品名「{product['name']}」", "在开头或标题中点明产品")
    if not ENTRY_HINT_RE.search(title + body):
        add("内容完整性", "warn", "未给出体验入口或行动引导",
            "补充官网/下载/试用入口（可配 UTM 链接）")

    # 7) 禁用表述（产品档案 red line）
    for phrase in filter(None, (p.strip() for p in re.split(r"[;；\n,，]", product["forbidden_claims"] or ""))):
        if phrase and phrase in (title + body):
            add("宣传红线", "error",
                f"使用了产品档案禁止的表述：「{phrase}」", "按档案红线改写")

    return checks
