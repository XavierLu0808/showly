// 产品推广平台 API（/api/promotion）——类型与请求函数
// 后端见 easel/promotion/router.py

function getBasePath(): string {
  const path = window.location.pathname;
  return path.replace(/\/index\.html$/, '').replace(/\/$/, '');
}

const BASE = getBasePath();

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, { cache: 'no-store', ...options });
  if (!res.ok) {
    let detail = '';
    try {
      const j = await res.json();
      detail = (j && (j.detail || j.message)) || '';
    } catch { /* 非 JSON 响应体 */ }
    throw new Error(detail || `请求失败（${res.status}）`);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

// ---------- 类型 ----------

export interface Product {
  id: string;
  name: string;
  tagline: string;
  website: string;
  language: string;
  logo_path: string;
  brand_color: string;
  default_persona: string;
  forbidden_claims: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
  facts_confirmed?: number;
  facts_total?: number;
  assets_total?: number;
  campaigns_total?: number;
}

export interface ProductFact {
  id: string;
  product_id: string;
  category: string;
  content: string;
  source: string;
  launch_status: 'launched' | 'beta' | 'planned' | 'unverified';
  confirmed: boolean;
}

export interface ProductVersion {
  id: string;
  version_label: string;
  confirmed: boolean;
  confirmed_at: string | null;
  created_at: string;
}

export interface Asset {
  id: string;
  product_id: string;
  kind: string;
  name: string;
  path: string;
  note: string;
  restrictions: string;
  created_at: string;
}

export interface ProductDetail extends Product {
  versions: ProductVersion[];
  facts: ProductFact[];
  assets: Asset[];
  campaigns: CampaignSummary[];
}

export interface CampaignSummary {
  id: string;
  product_id: string;
  template: 'launch' | 'feature_update' | 'tutorial';
  goal: string;
  audience: string;
  channels: string[];
  status: string;
  plan_date: string | null;
  brief: string;
  brief_confirmed: boolean;
  updated_at: string;
}

export interface ContentItem {
  id: string;
  campaign_id: string;
  platform: string;
  format: string;
  angle: string;
  status: 'draft' | 'generating' | 'ready' | 'reviewed' | 'exported' | 'published';
  current_revision: number;
  latest_revision?: ContentRevision | null;
}

export interface ContentRevision {
  id: string;
  revision_no: number;
  title: string;
  body: string;
  structure: Record<string, unknown>;
  asset_refs: string[];
  review: {
    approved: boolean;
    invalidated_at: string | null;
    checks?: CheckIssue[];
    approved_at?: string | null;
  } | null;
  created_at: string;
}

export interface CheckIssue {
  item: string;
  severity: 'error' | 'warn';
  message: string;
  suggestion: string;
}

export interface GenerationJob {
  id: string;
  campaign_id: string | null;
  content_id: string | null;
  job_type: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  error: string;
  steps: Array<Record<string, unknown>>;
  created_at: string;
}

export interface Publication {
  id: string;
  content_id: string;
  campaign_id: string;
  channel: string;
  account: string;
  link: string;
  status: string;
  confirm_source: string;
  published_at: string | null;
  created_at: string;
}

export interface CampaignDetail extends CampaignSummary {
  angles: Array<{
    angle: string; audience: string; hook: string; value: string;
    formats: string[]; cta: string; selected?: boolean;
  }>;
  contents: ContentItem[];
  jobs: GenerationJob[];
  publications: Publication[];
  metrics_summary: { by_metric: Record<string, Array<Record<string, unknown>>> };
}

export interface ContentDetail extends ContentItem {
  revisions: ContentRevision[];
  assets: Asset[];
}

// ---------- 产品 ----------

export function fetchProducts(): Promise<Product[]> {
  return request<{ products: Product[] }>('/api/promotion/products').then((r) => r.products);
}

export function fetchProduct(id: string): Promise<ProductDetail> {
  return request<{ product: ProductDetail }>(`/api/promotion/products/${id}`).then((r) => r.product);
}

export function createProduct(input: Partial<Product> & { name: string }): Promise<ProductDetail> {
  return request<{ product: ProductDetail }>('/api/promotion/products', json('POST', input)).then((r) => r.product);
}

export function updateProduct(id: string, input: Partial<Product>): Promise<ProductDetail> {
  return request<{ product: ProductDetail }>(`/api/promotion/products/${id}`, json('PUT', input)).then((r) => r.product);
}

export function addFact(productId: string, input: Partial<ProductFact> & { category: string; content: string }): Promise<ProductFact> {
  return request<{ fact: ProductFact }>(`/api/promotion/products/${productId}/facts`, json('POST', input)).then((r) => r.fact);
}

export function updateFact(productId: string, factId: string, input: Partial<ProductFact>): Promise<ProductFact> {
  return request<{ fact: ProductFact }>(`/api/promotion/products/${productId}/facts/${factId}`, json('PUT', input)).then((r) => r.fact);
}

export function createVersion(productId: string, versionLabel: string): Promise<ProductVersion> {
  return request<{ version: ProductVersion }>(`/api/promotion/products/${productId}/versions`, json('POST', { version_label: versionLabel })).then((r) => r.version);
}

export function confirmVersion(productId: string, versionId: string): Promise<ProductVersion> {
  return request<{ version: ProductVersion }>(`/api/promotion/products/${productId}/versions/${versionId}/confirm`, json('POST', {})).then((r) => r.version);
}

// ---------- 素材 ----------

export function uploadAssets(productId: string, files: File[]): Promise<Array<{ name: string; path: string }>> {
  const form = new FormData();
  for (const f of files) form.append('files', f);
  return request<{ files: Array<{ name: string; path: string }> }>(`/api/promotion/products/${productId}/assets/upload`, { method: 'POST', body: form }).then((r) => r.files);
}

export function registerAsset(productId: string, input: { kind: string; name: string; path: string; note?: string }): Promise<Asset> {
  return request<{ asset: Asset }>(`/api/promotion/products/${productId}/assets`, json('POST', input)).then((r) => r.asset);
}

export function deleteAsset(productId: string, assetId: string): Promise<void> {
  return request(`/api/promotion/products/${productId}/assets/${assetId}`, { method: 'DELETE' });
}

// ---------- 活动 ----------

export function fetchCampaigns(productId?: string): Promise<CampaignSummary[]> {
  const q = productId ? `?product_id=${encodeURIComponent(productId)}` : '';
  return request<{ campaigns: CampaignSummary[] }>(`/api/promotion/campaigns${q}`).then((r) => r.campaigns);
}

export function fetchCampaign(id: string): Promise<CampaignDetail> {
  return request<{ campaign: CampaignDetail }>(`/api/promotion/campaigns/${id}`).then((r) => r.campaign);
}

export function createCampaign(input: {
  product_id: string; template: string; goal?: string; audience?: string;
  channels?: string[]; voice?: string; cta?: string; plan_date?: string | null;
}): Promise<CampaignDetail> {
  return request<{ campaign: CampaignDetail }>('/api/promotion/campaigns', json('POST', input)).then((r) => r.campaign);
}

export function updateCampaign(id: string, input: Record<string, unknown>): Promise<CampaignDetail> {
  return request<{ campaign: CampaignDetail }>(`/api/promotion/campaigns/${id}`, json('PUT', input)).then((r) => r.campaign);
}

export function startCampaignJob(campaignId: string, jobType: 'brief' | 'angles'): Promise<GenerationJob> {
  return request<{ job: GenerationJob }>(`/api/promotion/campaigns/${campaignId}/jobs/${jobType}`, json('POST', {})).then((r) => r.job);
}

export function confirmPlan(campaignId: string): Promise<{ created: ContentItem[] }> {
  return request<{ created: ContentItem[] }>(`/api/promotion/campaigns/${campaignId}/confirm-plan`, json('POST', {}));
}

// ---------- 内容 ----------

export function fetchContent(id: string): Promise<ContentDetail> {
  return request<{ content: ContentDetail }>(`/api/promotion/contents/${id}`).then((r) => r.content);
}

export function createContent(input: {
  campaign_id: string; platform: string; format: string; angle?: string;
}): Promise<ContentDetail> {
  return request<{ content: ContentDetail }>('/api/promotion/contents', json('POST', input)).then((r) => r.content);
}

export function editContent(id: string, input: {
  title?: string; body?: string; structure?: Record<string, unknown>; asset_refs?: string[];
}): Promise<ContentDetail> {
  return request<{ content: ContentDetail }>(`/api/promotion/contents/${id}`, json('PUT', input)).then((r) => r.content);
}

export function startContentJob(contentId: string, jobType: 'content_draft' | 'checks'): Promise<GenerationJob> {
  return request<{ job: GenerationJob }>(`/api/promotion/contents/${contentId}/jobs/${jobType}`, json('POST', {})).then((r) => r.job);
}

export function runChecks(contentId: string): Promise<{ issues: CheckIssue[] }> {
  return request<{ issues: CheckIssue[] }>(`/api/promotion/contents/${contentId}/check`, json('POST', {}));
}

export function reviewContent(contentId: string, approved: boolean, revisionId: string): Promise<ContentDetail> {
  return request<{ content: ContentDetail }>(`/api/promotion/contents/${contentId}/review`, json('POST', { approved, revision_id: revisionId })).then((r) => r.content);
}

// ---------- 任务 / 发布 / 指标 ----------

export function fetchJob(jobId: string): Promise<GenerationJob> {
  return request<{ job: GenerationJob }>(`/api/promotion/jobs/${jobId}`).then((r) => r.job);
}

export function retryJob(jobId: string): Promise<GenerationJob> {
  return request<{ job: GenerationJob }>(`/api/promotion/jobs/${jobId}/retry`, json('POST', {})).then((r) => r.job);
}

export function cancelJob(jobId: string): Promise<GenerationJob> {
  return request<{ job: GenerationJob }>(`/api/promotion/jobs/${jobId}/cancel`, json('POST', {})).then((r) => r.job);
}

export function backfillPublication(contentId: string, input: {
  channel: string; link: string; account?: string; published_at?: string | null; note?: string;
}): Promise<Publication> {
  return request<{ publication: Publication }>(`/api/promotion/contents/${contentId}/publications`, json('POST', input)).then((r) => r.publication);
}

export async function exportCampaignZip(campaignId: string): Promise<void> {
  const res = await fetch(`${BASE}/api/promotion/campaigns/${campaignId}/export-zip`, {
    method: 'POST', cache: 'no-store',
  });
  if (!res.ok) {
    let detail = '';
    try {
      const error = await res.json();
      detail = error.detail || error.message || '';
    } catch { /* 非 JSON 错误使用状态码提示 */ }
    throw new Error(detail || `导出失败（${res.status}）`);
  }
  const disposition = res.headers.get('Content-Disposition') || '';
  const encoded = disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename\s*=\s*(?:"([^"]+)"|([^;]+))/i);
  let filename = plain?.[1] || plain?.[2]?.trim() || `campaign-${campaignId}.zip`;
  if (encoded) {
    try { filename = decodeURIComponent(encoded.trim()); }
    catch { /* 无效编码沿用普通文件名 */ }
  }
  filename = filename.split(/[\\/]/).pop()?.replace(/\p{Cc}/gu, '') || `campaign-${campaignId}.zip`;
  const url = URL.createObjectURL(await res.blob());
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  try {
    document.body.appendChild(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    // 浏览器开始读取下载后再释放 URL；失败路径也会清理。
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export function addMetric(campaignId: string, input: {
  metric: string; value: number; platform?: string; window_start?: string; window_end?: string;
}): Promise<void> {
  return request(`/api/promotion/campaigns/${campaignId}/metrics`, json('POST', input));
}

export function createTrackingLink(campaignId: string, input: {
  target_url: string; utm_source?: string; content_id?: string | null;
}): Promise<{ full_url: string }> {
  return request<{ link: { full_url: string } }>(`/api/promotion/campaigns/${campaignId}/tracking-links`, json('POST', input)).then((r) => r.link);
}

// ---------- 常量 ----------

export const TEMPLATE_LABELS: Record<string, string> = {
  launch: '首次上线', feature_update: '功能更新', tutorial: '场景教程',
};

export const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书', 'wechat-oa': '公众号', 'short-video': '短视频',
};

export const FORMAT_LABELS: Record<string, string> = {
  note: '图文笔记', card_set: '卡片组', article: '文章', video: '演示视频',
};

export const STATUS_LABELS: Record<string, string> = {
  draft: '草稿', generating: '生成中', ready: '待审核', reviewed: '已审核',
  exported: '已导出', published: '已发布',
};

export const LAUNCH_STATUS_LABELS: Record<string, string> = {
  launched: '已上线', beta: '测试中', planned: '计划中', unverified: '待核实',
};

export const FACT_CATEGORY_LABELS: Record<string, string> = {
  audience: '目标用户', pain_point: '痛点', use_case: '使用场景', feature: '功能',
  differentiator: '差异化', pricing: '定价', limit: '使用限制', entry: '体验入口',
  evidence: '证明材料', other: '其他',
};

export const ASSET_KIND_LABELS: Record<string, string> = {
  brand: '品牌素材', screenshot: '产品截图', recording: '演示录屏',
  evidence: '证明材料', document: '文档',
};
