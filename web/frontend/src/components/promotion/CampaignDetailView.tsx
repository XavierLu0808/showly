import { useState, useEffect, useCallback } from 'react';
import {
  fetchCampaign, startCampaignJob, confirmPlan, fetchJob, retryJob, cancelJob,
  backfillPublication, exportCampaignZip, addMetric, createTrackingLink,
  TEMPLATE_LABELS, PLATFORM_LABELS, FORMAT_LABELS, STATUS_LABELS,
  type CampaignDetail, type GenerationJob,
} from '../../lib/promotionApi';

interface Props {
  campaignId: string;
  productId: string;
  onBack: () => void;
  onOpenContent: (contentId: string) => void;
  onToast: (message: string) => void;
}

// 任务等待：生成任务在后台线程执行，前端轮询到终态
function useJobWaiter(onToast: (m: string) => void) {
  return useCallback(async (job: GenerationJob, onDone: () => void) => {
    for (let i = 0; i < 300; i++) {
      await new Promise((r) => setTimeout(r, 500));
      try {
        const j = await fetchJob(job.id);
        if (j.status === 'succeeded' || j.status === 'failed' || j.status === 'cancelled') {
          if (j.status === 'failed') onToast(`任务失败：${j.error.split('\n')[0]}`);
          onDone();
          return;
        }
      } catch { /* 轮询失败继续 */ }
    }
    onDone();
  }, [onToast]);
}

export default function CampaignDetailView({ campaignId, onBack, onOpenContent, onToast }: Props) {
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [pubLink, setPubLink] = useState('');
  const [metricName, setMetricName] = useState('views');
  const [metricValue, setMetricValue] = useState('');
  const [utmUrl, setUtmUrl] = useState('');

  const load = useCallback(() => {
    fetchCampaign(campaignId).then(setCampaign).catch((e) => onToast(String(e.message || e)));
  }, [campaignId, onToast]);
  useEffect(() => { load(); }, [load]);

  const waitJob = useJobWaiter(onToast);

  const runCampaignJob = async (type: 'brief' | 'angles') => {
    if (busy) return;
    setBusy(true);
    try {
      const job = await startCampaignJob(campaignId, type);
      waitJob(job, () => { setBusy(false); load(); });
    } catch (e) {
      setBusy(false);
      onToast(String((e as Error).message || e));
    }
  };

  const doConfirmPlan = async () => {
    try {
      const res = await confirmPlan(campaignId);
      onToast(`已按所选角度生成 ${res.created.length} 项内容任务`);
      load();
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doRetry = async (jobId: string) => {
    try { const job = await retryJob(jobId); waitJob(job, load); }
    catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doCancel = async (jobId: string) => {
    try { await cancelJob(jobId); load(); }
    catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doBackfill = async (contentId: string, channel: string) => {
    if (!pubLink.trim()) { onToast('先粘贴作品链接'); return; }
    try {
      await backfillPublication(contentId, { channel, link: pubLink.trim() });
      setPubLink(''); onToast('已回填发布记录（人工确认，未经平台自动核验）');
      load();
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doAddMetric = async () => {
    const value = Number(metricValue);
    if (!metricValue || Number.isNaN(value)) return;
    try {
      await addMetric(campaignId, { metric: metricName, value });
      setMetricValue(''); onToast('指标已录入'); load();
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doUtm = async () => {
    if (!campaign) return;
    try {
      const link = await createTrackingLink(campaignId, {
        target_url: utmUrl.trim(), utm_source: 'xiaohongshu',
      });
      await navigator.clipboard?.writeText(link.full_url).catch(() => {});
      onToast(`UTM 链接已生成并复制：${link.full_url}`);
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doExport = async () => {
    if (exporting) return;
    setExporting(true);
    setExportError('');
    try {
      await exportCampaignZip(campaignId);
      load();
    } catch (e) {
      const message = String((e as Error).message || e);
      setExportError(message);
      onToast(message);
    } finally {
      setExporting(false);
    }
  };

  if (!campaign) return <div className="empty-hint">加载中…</div>;

  const reviewedCount = campaign.contents.filter((c) =>
    ['reviewed', 'exported', 'published'].includes(c.status)).length;

  return (
    <>
      <div className="page-head">
        <div>
          <button className="btn btn-sm" style={{ marginBottom: 8 }} onClick={onBack}>← 返回</button>
          <h1 className="page-title">📣 {TEMPLATE_LABELS[campaign.template]} 活动</h1>
          <p className="page-subtitle">
            受众：{campaign.audience || '未设置'} · 渠道：{campaign.channels.map((c) => PLATFORM_LABELS[c] || c).join('、') || '未设置'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-sm" disabled={exporting || reviewedCount === 0}
            title={reviewedCount === 0 ? '需要至少一条已审核内容' : undefined}
            onClick={doExport}>{exporting ? '导出中…' : '⬇ 导出宣传包 ZIP'}</button>
        </div>
      </div>
      {exportError && <div className="promotion-issue promotion-issue-error" role="alert">{exportError}</div>}

      {/* 活动简报 */}
      <div className="card" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 600 }}>活动简报</div>
          <button className="btn btn-sm" disabled={busy} onClick={() => runCampaignJob('brief')}>
            {busy ? '生成中…' : campaign.brief ? '重新生成' : '生成简报'}
          </button>
        </div>
        {campaign.brief ? (
          <div style={{ fontSize: 13, whiteSpace: 'pre-wrap', lineHeight: 1.7, color: 'var(--text-secondary)' }}>
            {campaign.brief}
          </div>
        ) : (
          <div className="empty-hint">先生成活动简报，确认后进入方案与制作</div>
        )}
      </div>

      {/* 宣传角度 */}
      <div className="card" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 600 }}>宣传角度与内容清单</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-sm" disabled={busy} onClick={() => runCampaignJob('angles')}>
              {busy ? '生成中…' : '生成角度'}
            </button>
            <button className="btn btn-sm btn-primary" disabled={campaign.angles.length === 0}
              onClick={doConfirmPlan}>确认方案 → 生成内容任务</button>
          </div>
        </div>
        {campaign.angles.length === 0 && <div className="empty-hint">还没有宣传角度</div>}
        {campaign.angles.map((a, i) => (
          <div key={i} className="promotion-angle">
            <div style={{ fontWeight: 600, fontSize: 13 }}>
              {a.selected !== false ? '✅' : '⬜'} {a.angle}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.6 }}>
              受众：{a.audience} · 切入：{a.hook} · 形式：{a.formats.map((f) => FORMAT_LABELS[f] || f).join('/')}
            </div>
          </div>
        ))}
      </div>

      {/* 内容清单 */}
      <div className="card" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>内容清单（{campaign.contents.length}）</div>
        {campaign.contents.length === 0 && <div className="empty-hint">确认方案后生成内容任务</div>}
        {campaign.contents.map((c) => (
          <div key={c.id} className="promotion-fact-row promotion-clickable" onClick={() => onOpenContent(c.id)}>
            <span className="badge">{PLATFORM_LABELS[c.platform] || c.platform}</span>
            <span style={{ flex: 1, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {FORMAT_LABELS[c.format] || c.format}
              {c.latest_revision?.title ? ` · ${c.latest_revision.title}` : ''}
            </span>
            <span className={`badge promotion-content-${c.status}`}>{STATUS_LABELS[c.status] || c.status}</span>
            {['reviewed', 'exported'].includes(c.status) && (
              <button className="btn btn-sm" style={{ marginLeft: 6 }} onClick={(e) => {
                e.stopPropagation();
                doBackfill(c.id, c.platform);
              }}>回填链接</button>
            )}
          </div>
        ))}
        {campaign.contents.some((c) => ['reviewed', 'exported'].includes(c.status)) && (
          <input className="field" style={{ marginTop: 8 }} value={pubLink.trim()} placeholder="粘贴已发布作品链接，再点对应内容的「回填链接」"
            onChange={(e) => setPubLink(e.target.value)} />
        )}
      </div>

      {/* 任务进度 */}
      {campaign.jobs.length > 0 && (
        <div className="card" style={{ padding: 16, marginBottom: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 10 }}>任务进度</div>
          {campaign.jobs.slice(0, 8).map((j) => (
            <div key={j.id} className="promotion-fact-row">
              <span className="badge">{j.job_type}</span>
              <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)' }}>
                {j.created_at.slice(11, 19)} {j.error ? `· ${j.error.split('\n')[0].slice(0, 60)}` : ''}
              </span>
              <span className={`badge promotion-job-${j.status}`}>
                {{ queued: '排队中', running: '运行中', succeeded: '成功', failed: '失败', cancelled: '取消' }[j.status]}
              </span>
              {(j.status === 'failed' || j.status === 'cancelled') && (
                <button className="session-act" title="重试" onClick={() => doRetry(j.id)}>↻</button>
              )}
              {(j.status === 'queued' || j.status === 'running') && (
                <button className="session-act danger" title="取消" onClick={() => doCancel(j.id)}>×</button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 发布记录 + 效果 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
        <div className="card" style={{ padding: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 10 }}>发布记录</div>
          {campaign.publications.length === 0 && <div className="empty-hint">暂无发布记录；导出后在官方平台提交，并回填链接</div>}
          {campaign.publications.map((p) => (
            <div key={p.id} className="promotion-fact-row">
              <span className="badge">{PLATFORM_LABELS[p.channel] || p.channel}</span>
              <span style={{ flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {p.link || '（无链接）'}
              </span>
              <span className="badge" title="人工确认，未经平台自动核验">
                {p.status === 'published' ? '已发布' : p.status} · 人工确认
              </span>
            </div>
          ))}
        </div>
        <div className="card" style={{ padding: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 10 }}>效果记录</div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
            <select className="field" style={{ width: 110 }} value={metricName} onChange={(e) => setMetricName(e.target.value)}>
              {['views', 'reads', 'likes', 'comments', 'shares', 'visits', 'registrations'].map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <input className="field" type="number" value={metricValue} placeholder="数值"
              onChange={(e) => setMetricValue(e.target.value)} />
            <button className="btn btn-sm" onClick={doAddMetric}>录入</button>
          </div>
          {Object.entries(campaign.metrics_summary.by_metric).map(([metric, rows]) => (
            <div key={metric} className="promotion-fact-row">
              <span className="badge">{metric}</span>
              <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)' }}>
                {rows.map((r) => `${r.platform || '全部'}: ${r.total}`).join(' · ')}
              </span>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <input className="field" value={utmUrl} placeholder="生成 UTM 链接的目标地址"
              onChange={(e) => setUtmUrl(e.target.value)} />
            <button className="btn btn-sm" onClick={doUtm} disabled={!utmUrl.trim()}>UTM</button>
          </div>
        </div>
      </div>
    </>
  );
}
