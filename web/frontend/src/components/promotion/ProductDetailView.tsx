import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchProduct, addFact, updateFact, createVersion, confirmVersion,
  uploadAssets, registerAsset, deleteAsset, createCampaign,
  FACT_CATEGORY_LABELS, LAUNCH_STATUS_LABELS, ASSET_KIND_LABELS, TEMPLATE_LABELS,
  type ProductDetail,
} from '../../lib/promotionApi';

interface Props {
  productId: string;
  onBack: () => void;
  onOpenCampaign: (campaignId: string, productId: string) => void;
  onToast: (message: string) => void;
}

type Tab = 'profile' | 'assets' | 'campaigns';

export default function ProductDetailView({ productId, onBack, onOpenCampaign, onToast }: Props) {
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [tab, setTab] = useState<Tab>('profile');
  const [factText, setFactText] = useState('');
  const [factCategory, setFactCategory] = useState('feature');
  const [factStatus, setFactStatus] = useState('launched');
  const [campaignTemplate, setCampaignTemplate] = useState('launch');
  const [campaignAudience, setCampaignAudience] = useState('');
  const [showCreateCampaign, setShowCreateCampaign] = useState(false);
  const [uploadKind, setUploadKind] = useState('screenshot');
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    fetchProduct(productId).then(setProduct).catch((e) => onToast(String(e.message || e)));
  }, [productId, onToast]);
  useEffect(() => { load(); }, [load]);

  if (!product) return <div className="empty-hint">加载中…</div>;

  const saveFact = async () => {
    if (!factText.trim()) return;
    try {
      await addFact(productId, {
        category: factCategory, content: factText.trim(), launch_status: factStatus as 'launched',
      });
      setFactText(''); onToast('已添加事实'); load();
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const toggleFact = async (factId: string, confirmed: boolean) => {
    try { await updateFact(productId, factId, { confirmed }); load(); }
    catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doCreateVersion = async () => {
    try {
      const v = await createVersion(productId, `v${product.versions.length + 1}`);
      await confirmVersion(productId, v.id);
      onToast('版本档案已确认；引用旧事实的已审核内容已标为待复核');
      load();
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const doUpload = async (files: FileList | null, kind: string) => {
    if (!files || files.length === 0) return;
    try {
      const saved = await uploadAssets(productId, Array.from(files));
      for (const f of saved) {
        await registerAsset(productId, { kind, name: f.name, path: f.path });
      }
      onToast(`已上传 ${saved.length} 个素材`);
      load();
    } catch (e) { onToast(String((e as Error).message || e)); }
    if (fileRef.current) fileRef.current.value = '';
  };

  const removeAsset = async (assetId: string) => {
    try { await deleteAsset(productId, assetId); load(); }
    catch (e) { onToast(String((e as Error).message || e)); }
  };

  const pickAssets = (kind: string) => {
    setUploadKind(kind);
    fileRef.current?.click();
  };

  const doCreateCampaign = async () => {
    try {
      const c = await createCampaign({
        product_id: productId, template: campaignTemplate, audience: campaignAudience.trim(),
        channels: ['xiaohongshu', 'wechat-oa', 'short-video'],
      });
      setShowCreateCampaign(false);
      onToast('活动已创建');
      onOpenCampaign(c.id, productId);
    } catch (e) { onToast(String((e as Error).message || e)); }
  };

  const confirmedCount = product.facts.filter((f) => f.confirmed).length;

  return (
    <>
      <div className="page-head">
        <div>
          <button className="btn btn-sm" style={{ marginBottom: 8 }} onClick={onBack}>← 返回</button>
          <h1 className="page-title">{product.name}</h1>
          <p className="page-subtitle">
            {product.tagline || '暂无介绍'}
            {product.website && <> · <a href={product.website} target="_blank" rel="noreferrer">{product.website}</a></>}
          </p>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 7, marginBottom: 14 }}>
        {([['profile', `产品档案 ${confirmedCount}/${product.facts.length}`], ['assets', `素材库 ${product.assets.length}`], ['campaigns', `推广活动 ${product.campaigns.length}`]] as Array<[Tab, string]>).map(([key, label]) => (
          <button key={key} className={`chip ${tab === key ? 'active' : ''}`} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>

      {tab === 'profile' && (
        <>
          <div className="card" style={{ padding: 16, marginBottom: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>档案事实</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10, lineHeight: 1.6 }}>
              只登记你能确认的产品事实；未确认项不会进入生成上下文。上线状态决定宣传口径——「计划中/测试中」的能力不会被写成已上线。
            </div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
              <select className="field" style={{ width: 120 }} value={factCategory}
                onChange={(e) => setFactCategory(e.target.value)}>
                {Object.entries(FACT_CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <select className="field" style={{ width: 110 }} value={factStatus}
                onChange={(e) => setFactStatus(e.target.value)}>
                {Object.entries(LAUNCH_STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="field" value={factText} placeholder="如：支持一键生成小红书图文卡片"
                onChange={(e) => setFactText(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && saveFact()} />
              <button className="btn btn-sm" onClick={saveFact} disabled={!factText.trim()}>添加</button>
            </div>
            {product.facts.length === 0 && <div className="empty-hint" style={{ marginTop: 10 }}>还没有事实记录</div>}
            {product.facts.map((f) => (
              <div key={f.id} className="promotion-fact-row">
                <span className={`badge promotion-status-${f.launch_status}`}>
                  {FACT_CATEGORY_LABELS[f.category] || f.category} · {LAUNCH_STATUS_LABELS[f.launch_status]}
                </span>
                <span style={{ flex: 1, fontSize: 13 }}>{f.content}</span>
                <button className={`chip ${f.confirmed ? 'active' : ''}`} style={{ fontSize: 11 }}
                  onClick={() => toggleFact(f.id, !f.confirmed)}>
                  {f.confirmed ? '已确认' : '待确认'}
                </button>
              </div>
            ))}
          </div>

          <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontWeight: 600 }}>版本档案快照</div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                  首次生成宣传内容前确认档案；确认后引用旧事实的已审核内容会标为待复核。
                </div>
              </div>
              <button className="btn btn-sm" onClick={doCreateVersion}>拍快照并确认</button>
            </div>
            {product.versions.length > 0 && (
              <div style={{ marginTop: 10 }}>
                {product.versions.map((v) => (
                  <div key={v.id} className="promotion-fact-row">
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{v.version_label}</span>
                    <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)' }}>
                      {v.created_at.slice(0, 19).replace('T', ' ')}
                    </span>
                    <span className={`badge ${v.confirmed ? 'promotion-status-launched' : ''}`}>
                      {v.confirmed ? `已确认 ${v.confirmed_at?.slice(0, 10) ?? ''}` : '未确认'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {tab === 'assets' && (
        <div className="card" style={{ padding: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>素材库</div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12, lineHeight: 1.6 }}>
            真实产品画面只能来自这里的截图/录屏；生成内容会明确引用所选素材，原始文件不会被覆盖。被内容引用的素材删除时会受到保护。
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <input ref={fileRef} type="file" multiple hidden
              onChange={(e) => doUpload(e.target.files, uploadKind)} />
            {(['screenshot', 'recording', 'brand', 'evidence'] as const).map((kind) => (
              <button key={kind} className="btn btn-sm" onClick={() => pickAssets(kind)}>
                + 上传{ASSET_KIND_LABELS[kind]}
              </button>
            ))}
          </div>
          {product.assets.length === 0 && <div className="empty-hint">还没有素材</div>}
          {product.assets.map((a) => (
            <div key={a.id} className="promotion-fact-row">
              <span className="badge">{ASSET_KIND_LABELS[a.kind] || a.kind}</span>
              <span style={{ flex: 1, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {a.name}
              </span>
              <button className="session-act danger" title="删除" onClick={() => removeAsset(a.id)}>×</button>
            </div>
          ))}
        </div>
      )}

      {tab === 'campaigns' && (
        <div className="card" style={{ padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <div style={{ fontWeight: 600 }}>推广活动</div>
            <button className="btn btn-sm btn-primary" onClick={() => setShowCreateCampaign(true)}>+ 创建活动</button>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
            一个产品可以有多个独立活动；不同活动的方案与内容互不影响。
          </div>
          {product.campaigns.length === 0 && <div className="empty-hint">还没有活动</div>}
          {product.campaigns.map((c) => (
            <div key={c.id} className="promotion-fact-row promotion-clickable"
              onClick={() => onOpenCampaign(c.id, productId)}>
              <span className="badge">{TEMPLATE_LABELS[c.template]}</span>
              <span style={{ flex: 1, fontSize: 13 }}>
                {c.audience || '未设置受众'} · {c.channels.join('/')}
              </span>
              <span className="badge">{c.updated_at.slice(0, 10)}</span>
            </div>
          ))}
        </div>
      )}

      {showCreateCampaign && (
        <div className="overlay" onClick={() => setShowCreateCampaign(false)}>
          <div className="modal" style={{ width: 420, maxWidth: '100%' }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 12px' }}>创建推广活动</h3>
            <label className="field-label">活动模板</label>
            <div style={{ display: 'flex', gap: 7, marginBottom: 10 }}>
              {Object.entries(TEMPLATE_LABELS).map(([k, v]) => (
                <button key={k} className={`chip ${campaignTemplate === k ? 'active' : ''}`}
                  onClick={() => setCampaignTemplate(k)}>{v}</button>
              ))}
            </div>
            <label className="field-label">目标受众</label>
            <input className="field" value={campaignAudience} placeholder="如：独立开发者 / 小团队"
              onChange={(e) => setCampaignAudience(e.target.value)} />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
              <button className="btn btn-sm" onClick={() => setShowCreateCampaign(false)}>取消</button>
              <button className="btn btn-sm btn-primary" onClick={doCreateCampaign}>创建并进入</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
