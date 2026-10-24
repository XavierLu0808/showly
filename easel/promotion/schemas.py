"""产品推广平台 API 模型（Pydantic）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------- 产品 ----------

class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tagline: str = ""
    website: str = ""
    language: str = "zh-CN"
    logo_path: str = ""
    brand_color: str = ""
    default_persona: str = ""
    forbidden_claims: str = ""


class ProductUpdate(BaseModel):
    name: str | None = None
    tagline: str | None = None
    website: str | None = None
    language: str | None = None
    logo_path: str | None = None
    brand_color: str | None = None
    default_persona: str | None = None
    forbidden_claims: str | None = None
    archived: bool | None = None


class FactInput(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1)
    source: str = "manual"
    launch_status: str = "unverified"


class FactUpdate(BaseModel):
    content: str | None = None
    source: str | None = None
    launch_status: str | None = None
    confirmed: bool | None = None


class VersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=60)


class AssetInput(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1)
    note: str = ""
    source: str = "user-upload"
    restrictions: str = ""
    version_id: str | None = None


# ---------- 活动 ----------

class CampaignCreate(BaseModel):
    product_id: str
    template: str = Field(pattern="^(launch|feature_update|tutorial)$")
    goal: str = "visit"
    audience: str = ""
    channels: list[str] = []
    voice: str = ""
    cta: str = ""
    plan_date: str | None = None
    version_id: str | None = None


class CampaignUpdate(BaseModel):
    goal: str | None = None
    audience: str | None = None
    channels: list[str] | None = None
    voice: str | None = None
    cta: str | None = None
    plan_date: str | None = None
    brief: str | None = None
    brief_confirmed: bool | None = None
    angles: list[dict] | None = None
    tasks: list[dict] | None = None
    status: str | None = None


# ---------- 内容 ----------

class ContentCreate(BaseModel):
    campaign_id: str
    platform: str
    format: str
    angle: str = ""
    title: str = ""
    body: str = ""


class ContentEdit(BaseModel):
    title: str | None = None
    body: str | None = None
    structure: dict | None = None
    asset_refs: list[str] | None = None


class ReviewDecision(BaseModel):
    revision_id: str | None = None
    approved: bool
    approved_by: str = "user"


# ---------- 任务 / 发布 / 指标 ----------

class JobRetry(BaseModel):
    params_override: dict = {}


class PublicationInput(BaseModel):
    channel: str
    account: str = ""
    link: str = ""
    published_at: str | None = None
    status: str = "published"
    note: str = ""


class MetricInput(BaseModel):
    metric: str
    value: float
    platform: str = ""
    unit: str = ""
    window_start: str = ""
    window_end: str = ""
    content_id: str | None = None


class MetricImportRow(BaseModel):
    metric: str
    value: float
    platform: str = ""
    unit: str = ""
    window_start: str = ""
    window_end: str = ""
    content_id: str | None = None


class MetricImport(BaseModel):
    batch: str = Field(min_length=1)
    rows: list[MetricImportRow]


class TrackingLinkInput(BaseModel):
    target_url: str
    content_id: str | None = None
    utm_source: str = ""
    utm_medium: str = "promotion"
