import { useState, useEffect, useCallback } from 'react';
import {
  fetchContent, editContent, startContentJob, runChecks, reviewContent, fetchJob,
  PLATFORM_LABELS, FORMAT_LABELS, STATUS_LABELS, ASSET_KIND_LABELS,
  type ContentDetail,
} from '../../lib/promotionApi';

interface Props {
  contentId: string;
  onBack: () => void;
  onToast: (message: string) => void;
}

export default function ContentEditorView({ contentId, onBack, onToast }: Props) {
  const [content, setContent] = useState<ContentDetail | null>(null);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const load = useCallback(() => {
    fetchContent(contentId).then((c) => {
      setContent(c);
      const latest = c.revisions[c.revisions.length - 1];
      if (!dirty) { setTitle(latest?.title || ''); setBody(latest?.body || ''); }
    }).catch((e) => onToast(String(e.message || e)));
  }, [contentId, dirty, onToast]);
  useEffect(() => { load(); }, [load]);

  if (!content) return <div className="empty-hint">加载中…</div>;

  const latest = content.revisions[content.revisions.length - 1];
  const activeReview = latest?.review && !latest.review.invalidated_at ? latest.review : null;
  const canReview = ['ready', 'reviewed', 'exported', 'published'].includes(content.status);
  const issues = (activeReview?.checks || []) as Array<{ item: string; severity: string; message: string; suggestion: string }>;
  const blocking = issues.filter((i) => i.severity === 'error');

  const waitJob = async (jobId: string) => {
    for (let i = 0; i < 300; i++) {
      await new Promise((r) => setTimeout(r, 500));
      try {
        const j = await fetchJob(jobId);
        if (['succeeded', 'failed', 'cancelled'].includes(j.status)) {
          if (j.status === 'failed') onToast(`任务失败：${j.error.split('\n')[0]}`);
          setBusy(false); setDirty(false); load();
          return;
        }
      } catch { /* 继续轮询 */ }
    }
    setBusy(false);
  };

  const doGenerate = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const job = await startContentJob(contentId, 'content_draft');
      waitJob(job.id);
    } catch (e) { setBusy(false); onToast(String((e as Error).message || e)); }
  };

  const doSave = async () => {
    try {
      await editContent(contentId, { title, body });
      setDirty(false); onToast('已保存为新修订；旧审核已失效');
      load();
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doCheck = async () => {
    if (dirty) { onToast('先保存修改再执行检查'); return; }
    try {
      const res = await runChecks(contentId);
      onToast(res.issues.length === 0 ? '检查通过，未发现问题' : `发现 ${res.issues.length} 个问题`);
      load();
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doReview = async (approved: boolean) => {
    if (dirty) { onToast('先保存修改再审核'); return; }
    try {
      await reviewContent(contentId, approved, latest.id);
      onToast(approved ? '已人工审核通过（绑定当前修订）' : '已撤销审核');
      load();
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const copyText = async () => {
    const text = `${title}\n\n${body}`;
    await navigator.clipboard?.writeText(text).catch(() => {});
    onToast('已复制标题与正文');
  };

  return (
    <>
      <div className="page-head">
        <div>
          <button className="btn btn-sm" style={{ marginBottom: 8 }} onClick={onBack}>← 返回活动</button>
          <h1 className="page-title">
            ✏️ {FORMAT_LABELS[content.format] || content.format}
            <span className="badge" style={{ marginLeft: 10, verticalAlign: 'middle' }}>
              {PLATFORM_LABELS[content.platform] || content.platform}
            </span>
            <span className={`badge promotion-content-${content.status}`} style={{ marginLeft: 6, verticalAlign: 'middle' }}>
              {STATUS_LABELS[content.status] || content.status}
            </span>
          </h1>
          <p className="page-subtitle">
            修订 {content.current_revision}
            {content.angle && ` · 角度：${content.angle}`}
            {activeReview?.approved && ' · 已人工审核（修订变化后失效）'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <button className="btn btn-sm" disabled={busy} onClick={doGenerate}>
            {busy ? '生成中…' : '🤖 AI 生成草稿'}
          </button>
          <button className="btn btn-sm" onClick={copyText}>复制文案</button>
          <button className="btn btn-sm" onClick={doCheck} disabled={dirty}>🔍 检查</button>
          {canReview && !blocking.length && (
            <button className="btn btn-sm btn-primary" onClick={() => doReview(true)} disabled={dirty}>
              ✅ 人工审核
            </button>
          )}
          {activeReview?.approved && (
            <button className="btn btn-sm" onClick={() => doReview(false)}>撤销审核</button>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: 12 }}>
        <div className="card" style={{ padding: 16 }}>
          <label className="field-label">标题</label>
          <input className="field" value={title} onChange={(e) => { setTitle(e.target.value); setDirty(true); }} />
          <label className="field-label" style={{ marginTop: 10 }}>
            正文 {content.format === 'video' && '（解说词/脚本）'}
          </label>
          <textarea className="field" style={{ minHeight: 320, fontFamily: 'inherit', lineHeight: 1.7 }}
            value={body} onChange={(e) => { setBody(e.target.value); setDirty(true); }} />
          <div style={{ display: 'flex', gap: 8, marginTop: 10, justifyContent: 'flex-end' }}>
            {dirty && <span style={{ fontSize: 12, color: 'var(--warn, #d97706)', alignSelf: 'center' }}>有未保存修改</span>}
            <button className="btn btn-sm" onClick={load} disabled={!dirty}>放弃修改</button>
            <button className="btn btn-sm btn-primary" onClick={doSave} disabled={!dirty}>保存修订</button>
          </div>

          {content.format === 'card_set' && latest?.structure && Array.isArray((latest.structure as { pages?: unknown[] }).pages) && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>卡片分页预览</div>
              <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 6 }}>
                {((latest.structure as { pages: Array<{ text?: string; screenshot_hint?: string }> }).pages || []).map((p, i) => (
                  <div key={i} className="promotion-card-preview">
                    <div style={{ fontWeight: 600, fontSize: 12 }}>P{i + 1}</div>
                    <div style={{ fontSize: 12, whiteSpace: 'pre-wrap', flex: 1 }}>{p.text}</div>
                    {p.screenshot_hint && p.screenshot_hint !== '无' && (
                      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>📷 {p.screenshot_hint}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div>
          {/* 检查结果 */}
          <div className="card" style={{ padding: 14, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>检查结果</div>
            {issues.length === 0 && <div className="empty-hint">{activeReview ? '检查通过，未发现问题' : '还没执行检查，点上方「检查」'}</div>}
            {issues.map((iss, i) => (
              <div key={i} className={`promotion-issue promotion-issue-${iss.severity}`}>
                <div style={{ fontWeight: 600, fontSize: 12 }}>
                  {iss.severity === 'error' ? '⛔' : '⚠️'} {iss.item}
                </div>
                <div style={{ fontSize: 12, marginTop: 2 }}>{iss.message}</div>
                {iss.suggestion && (
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>建议：{iss.suggestion}</div>
                )}
              </div>
            ))}
            {blocking.length > 0 && (
              <div style={{ fontSize: 12, color: 'var(--danger, #dc2626)', marginTop: 6 }}>
                存在 {blocking.length} 个必须处理的问题，处理并重新检查后才能审核通过。
              </div>
            )}
          </div>

          {/* 素材引用 */}
          <div className="card" style={{ padding: 14, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>产品素材（生成时自动引用）</div>
            {content.assets.length === 0 && <div className="empty-hint">产品还没有素材</div>}
            {content.assets.slice(0, 6).map((a) => (
              <div key={a.id} className="promotion-fact-row">
                <span className="badge">{ASSET_KIND_LABELS[a.kind] || a.kind}</span>
                <span style={{ flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
              </div>
            ))}
          </div>

          {/* 修订历史 */}
          <div className="card" style={{ padding: 14 }}>
            <button className="btn btn-sm" style={{ width: '100%' }} onClick={() => setShowHistory(!showHistory)}>
              修订历史（{content.revisions.length}）{showHistory ? '▲' : '▼'}
            </button>
            {showHistory && content.revisions.slice().reverse().map((r) => (
              <div key={r.id} className="promotion-fact-row">
                <span className="badge">v{r.revision_no}</span>
                <span style={{ flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.title || '（无标题）'}
                </span>
                <span className={`badge ${r.review?.approved && !r.review.invalidated_at ? 'promotion-status-launched' : ''}`}>
                  {r.review?.approved && !r.review.invalidated_at ? '已审核' : r.review?.invalidated_at ? '已失效' : '未审核'}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
