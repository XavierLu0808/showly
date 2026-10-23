"""生成服务：LLM 可用则用模型，未配置/失败时降级为档案模板草稿并明确标注。

覆盖四类输出：活动简报、宣传角度、小红书图文、公众号文章、演示视频脚本。
所有输出遵守事实约束：只用已确认事实，未知信息标「待补充」。
"""
from __future__ import annotations

import asyncio
import json

from . import service

SYSTEM_PROMPT = (
    "你是产品宣传内容策划助手。只依据提供的产品档案与素材写内容，"
    "档案中标注「planned/beta」的能力不得写成已上线；档案没有的信息写「待补充」，"
    "不得编造价格、用户评价和效果数字。输出 JSON。"
)


def _llm_available() -> bool:
    try:
        from easel.direct_api import api_config, read_settings
        api_config(read_settings())
        return True
    except Exception:
        return False


def _llm_json(prompt: str, timeout: float = 120.0) -> dict | None:
    """调用直连 API 让模型输出 JSON；失败返回 None（调用方降级）。"""
    if not _llm_available():
        return None
    try:
        from easel.direct_api import read_settings, stream_chat

        async def _run() -> str:
            chunks: list[str] = []
            async for _kind, text in stream_chat(read_settings(), prompt,
                                                 system=SYSTEM_PROMPT, timeout=timeout):
                if _kind == "token":
                    chunks.append(text)
            return "".join(chunks)

        raw = asyncio.run(_run())
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        return json.loads(raw[start:end + 1])
    except Exception:
        return None


def build_context(campaign_id: str) -> dict:
    """构建产品上下文：已确认事实、活动配置、可用素材。"""
    campaign = service.get_campaign(campaign_id)
    product = service.get_product(campaign["product_id"])
    confirmed_facts = [f for f in product["facts"] if f["confirmed"]]
    return {
        "product": {k: product[k] for k in
                    ("id", "name", "tagline", "website", "language", "brand_color",
                     "forbidden_claims")},
        "facts": confirmed_facts,
        "pending_facts": [f for f in product["facts"] if not f["confirmed"]],
        "assets": product["assets"],
        "campaign": {k: campaign[k] for k in
                     ("id", "template", "goal", "audience", "channels", "voice", "cta")},
    }


TEMPLATE_LABELS = {
    "launch": "首次上线", "feature_update": "功能更新", "tutorial": "场景教程",
}


def _fact_lines(context: dict) -> str:
    lines = []
    for fact in context["facts"]:
        status = {"launched": "已上线", "beta": "测试中", "planned": "计划中",
                  "unverified": "待核实"}.get(fact["launch_status"], fact["launch_status"])
        lines.append(f"- [{fact['category']}|{status}] {fact['content']}")
    if not lines:
        lines.append("- （暂无已确认事实，以下内容中的事实均为待补充）")
    return "\n".join(lines)


def _entry_line(context: dict) -> str:
    website = context["product"]["website"] or "待补充（官网/下载入口）"
    return website


# ---------------------------------------------------------------- 活动简报

def generate_brief(campaign_id: str) -> dict:
    context = build_context(campaign_id)
    prompt = (
        f"为产品写一份推广活动简报，输出 JSON：{{\"brief\": \"...\"}}。\n"
        f"产品：{context['product']['name']}，{context['product']['tagline']}\n"
        f"活动类型：{TEMPLATE_LABELS.get(context['campaign']['template'])}\n"
        f"目标受众：{context['campaign']['audience'] or '待补充'}\n"
        f"已确认事实：\n{_fact_lines(context)}\n"
        f"体验入口：{_entry_line(context)}\n"
        "简报包含：活动主题、目标、渠道侧重、内容清单建议、注意事项。300 字内。"
    )
    result = _llm_json(prompt)
    brief = (result or {}).get("brief") if isinstance(result, dict) else None
    mode = "model"
    if not brief:
        mode = "template"
        name = context["product"]["name"]
        template = TEMPLATE_LABELS.get(context["campaign"]["template"], "推广")
        brief = (
            f"# {name}{template}活动简报\n\n"
            f"- 主题：{context['product']['tagline'] or '待补充'}\n"
            f"- 目标：{context['campaign']['goal']}（访问/体验注册/收集反馈）\n"
            f"- 受众：{context['campaign']['audience'] or '待补充'}\n"
            f"- 渠道：{'、'.join(context['campaign']['channels']) or '小红书、公众号、短视频'}\n"
            f"- 体验入口：{_entry_line(context)}\n\n"
            "建议内容清单：小红书图文 1 篇、公众号介绍文章 1 篇、演示短视频 1 条。\n"
            "注意：本简报由模板基于产品档案生成，请在确认前补全待补充项并核对事实。"
        )
    return {"brief": brief, "generation_mode": mode}


# ---------------------------------------------------------------- 宣传角度

def generate_angles(campaign_id: str) -> dict:
    context = build_context(campaign_id)
    prompt = (
        "基于产品档案提出 3 个差异化的宣传角度（不同场景或叙述，不是同义改写），"
        '输出 JSON：{"angles": [{"angle": "...", "audience": "...", "hook": "...",'
        ' "value": "...", "evidence": "...", "formats": ["note|card_set|article|video"],'
        ' "cta": "..."}]}\n'
        f"产品：{context['product']['name']}——{context['product']['tagline']}\n"
        f"已确认事实：\n{_fact_lines(context)}\n"
    )
    result = _llm_json(prompt)
    angles = (result or {}).get("angles") if isinstance(result, dict) else None
    mode = "model"
    if not isinstance(angles, list) or not angles:
        mode = "template"
        name = context["product"]["name"]
        top_features = [f for f in context["facts"]
                        if f["category"] == "feature" and f["launch_status"] == "launched"]
        feature_text = top_features[0]["content"] if top_features else "核心功能（待补充）"
        angles = [
            {"angle": "痛点切入", "audience": context["campaign"]["audience"] or "目标用户",
             "hook": f"还在被这个问题困扰吗？——用 {name} 解决",
             "value": context["product"]["tagline"] or "待补充",
             "evidence": "（引用已确认事实/实测记录，未登记则不写数字）",
             "formats": ["note", "card_set"], "cta": context["campaign"]["cta"] or "访问官网体验"},
            {"angle": "功能演示", "audience": "正在选型的用户",
             "hook": f"{name} 的{feature_text[:18]}是这样工作的",
             "value": feature_text,
             "evidence": "（配合产品截图/录屏）",
             "formats": ["card_set", "video", "article"], "cta": "按教程上手"},
            {"angle": "场景故事", "audience": context["campaign"]["audience"] or "目标用户",
             "hook": f"我把 {name} 用在了真实工作流里",
             "value": "真实使用场景与结果",
             "evidence": "（需授权的用户反馈或实测记录）",
             "formats": ["note", "article"], "cta": context["campaign"]["cta"] or "申请试用"},
        ]
    return {"angles": angles, "generation_mode": mode}


# ---------------------------------------------------------------- 内容草稿

def _content_prompt(context: dict, platform: str, fmt: str, angle: dict | None) -> str:
    return (
        f"为产品写{platform}/{fmt}宣传内容。输出 JSON："
        '{"title": "...", "body": "...", "structure": {...}}\n'
        f"产品：{context['product']['name']}——{context['product']['tagline']}\n"
        f"宣传角度：{json.dumps(angle, ensure_ascii=False) if angle else '默认产品介绍'}\n"
        f"已确认事实：\n{_fact_lines(context)}\n"
        f"体验入口：{_entry_line(context)}\n"
        "小红书：标题≤20字，正文≤1000字，口语化带话题；"
        "卡片 structure.pages 为 5 页，每页 {text, screenshot_hint}；"
        "公众号：完整正文含操作步骤；"
        "视频 structure.script 为带步骤与时长估算的解说词。"
    )


def generate_content(content_id: str) -> dict:
    """生成内容草稿：优先模型，降级模板；返回 title/body/structure。"""
    content = service.get_content(content_id)
    context = build_context(content["campaign_id"])
    angle = None
    if content.get("angle"):
        campaign = service.get_campaign(content["campaign_id"])
        angle = next((a for a in campaign.get("angles", [])
                      if a.get("angle") == content["angle"]), None)
    result = _llm_json(_content_prompt(context, content["platform"], content["format"], angle))
    mode = "model"
    if not isinstance(result, dict) or not result.get("body"):
        mode = "template"
        result = _template_content(context, content, angle)
    return {
        "title": str(result.get("title") or "")[:80],
        "body": str(result.get("body") or ""),
        "structure": result.get("structure") or {},
        "generation_mode": mode,
    }


def _template_content(context: dict, content: dict, angle: dict | None) -> dict:
    name = context["product"]["name"]
    tagline = context["product"]["tagline"] or "（待补充一句话介绍）"
    entry = _entry_line(context)
    features = [f["content"] for f in context["facts"]
                if f["category"] == "feature" and f["launch_status"] == "launched"]
    feature_lines = "\n".join(f"- {f}" for f in features) or "- （待补充核心功能）"
    hook = (angle or {}).get("hook") or f"{name} 上线了：{tagline}"
    fmt, platform = content["format"], content["platform"]

    if platform == "xiaohongshu":
        body = (
            f"{hook}\n\n"
            f"{tagline}\n\n"
            f"核心功能（均来自官方档案）：\n{feature_lines}\n\n"
            f"体验入口：{entry}\n\n"
            "说明：这是基于产品档案的模板草稿，发布前请补全待补充项、核对事实并人工审核。\n"
            "#产品推荐 #效率工具"
        )
        pages = [{"text": f"{name}\n{tagline}", "screenshot_hint": "产品首页截图"},
                 {"text": "核心功能 1", "screenshot_hint": features[0] if features else "功能截图"},
                 {"text": "核心功能 2", "screenshot_hint": "功能截图"},
                 {"text": "上手很简单", "screenshot_hint": "操作步骤截图"},
                 {"text": f"体验入口\n{entry}", "screenshot_hint": "无"}]
        return {"title": f"{name}：{tagline[:14]}", "body": body,
                "structure": {"pages": pages} if fmt == "card_set" else {}}
    if platform == "wechat-oa":
        body = (
            f"# {name}：{tagline}\n\n"
            "## 解决什么问题\n（依据产品档案的受众与痛点事实，待补充则如实说明）\n\n"
            f"## 核心功能\n{feature_lines}\n\n"
            "## 如何开始使用\n"
            f"1. 打开 {entry}\n2. 按引导完成初始设置\n3. 开始使用核心功能\n\n"
            "## 定价与限制\n（按已确认定价事实填写；未确认则写「定价信息待确认」）\n\n"
            "---\n本文为产品介绍，能力状态以官方档案为准。"
        )
        return {"title": f"{name}：{tagline[:30]}", "body": body, "structure": {}}
    # 短视频演示脚本
    script = [
        {"step": 1, "narration": f"大家好，这是 {name}，{tagline}", "clip": "产品首页",
         "seconds": 5},
        {"step": 2, "narration": features[0] if features else "（待补充核心功能演示）",
         "clip": "核心功能录屏片段", "seconds": 15},
        {"step": 3, "narration": "操作很简单，三步就能完成", "clip": "操作步骤录屏",
         "seconds": 15},
        {"step": 4, "narration": f"体验入口见简介：{entry}", "clip": "结尾页", "seconds": 5},
    ]
    body = "\n".join(
        f"[{s['step']}] ({s['seconds']}s, {s['clip']}) {s['narration']}" for s in script)
    return {"title": f"{name} 演示", "body": body, "structure": {"script": script}}
