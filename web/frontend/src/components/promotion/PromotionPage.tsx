import { useState, useEffect, useCallback } from 'react';
import {
  fetchProducts, createProduct,
  type Product,
} from '../../lib/promotionApi';
import ProductDetailView from './ProductDetailView';
import CampaignDetailView from './CampaignDetailView';
import ContentEditorView from './ContentEditorView';

// 内部导航：产品列表 → 产品概览 → 活动详情 → 内容编辑器
type View =
  | { kind: 'list' }
  | { kind: 'product'; productId: string }
  | { kind: 'campaign'; campaignId: string; productId: string }
  | { kind: 'content'; contentId: string; campaignId: string; productId: string };

interface PromotionPageProps {
  onToast?: (message: string) => void;
}

export default function PromotionPage({ onToast }: PromotionPageProps) {
  const [view, setView] = useState<View>({ kind: 'list' });
  const [notice, setNotice] = useState('');
  const localToast = useCallback((message: string) => setNotice(message), []);
  const toast = onToast || localToast;

  return (
    <div className="page-scroll">
      {notice && <div role="status" className="card" style={{ padding: 12, marginBottom: 12 }}>
        {notice}<button className="btn btn-sm" style={{ marginLeft: 8 }} onClick={() => setNotice('')}>关闭</button>
      </div>}
      {view.kind === 'list' && <ProductsView onOpen={(id) => setView({ kind: 'product', productId: id })} onToast={toast} />}
      {view.kind === 'product' && (
        <ProductDetailView
          productId={view.productId}
          onBack={() => setView({ kind: 'list' })}
          onOpenCampaign={(campaignId, productId) => setView({ kind: 'campaign', campaignId, productId })}
          onToast={toast}
        />
      )}
      {view.kind === 'campaign' && (
        <CampaignDetailView
          campaignId={view.campaignId}
          productId={view.productId}
          onBack={() => setView({ kind: 'product', productId: view.productId })}
          onOpenContent={(contentId) => setView({
            kind: 'content', contentId, campaignId: view.campaignId, productId: view.productId,
          })}
          onToast={toast}
        />
      )}
      {view.kind === 'content' && (
        <ContentEditorView
          contentId={view.contentId}
          onBack={() => setView({ kind: 'campaign', campaignId: view.campaignId, productId: view.productId })}
          onToast={toast}
        />
      )}
    </div>
  );
}

function ProductsView({ onOpen, onToast }: {
  onOpen: (productId: string) => void;
  onToast: (message: string) => void;
}) {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [tagline, setTagline] = useState('');
  const [website, setWebsite] = useState('');

  const load = useCallback(() => {
    fetchProducts().then(setProducts).catch(() => {}).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!name.trim()) return;
    try {
      const p = await createProduct({ name: name.trim(), tagline: tagline.trim(), website: website.trim() });
      setCreating(false); setName(''); setTagline(''); setWebsite('');
      onToast('产品已创建，接下来补充产品档案');
      onOpen(p.id);
    } catch (e) {
      onToast(String((e as Error).message || e));
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">🚀 我的产品</h1>
          <p className="page-subtitle">
            管理多个产品的档案、素材与推广活动——从产品资料到可发布宣传包的一条流程。
          </p>
        </div>
        <button className="btn btn-sm btn-primary" onClick={() => setCreating(true)}>+ 添加产品</button>
      </div>

      {loading && <div className="empty-hint">加载中…</div>}
      {!loading && products.length === 0 && (
        <div className="card" style={{ padding: 28, textAlign: 'center' }}>
          <div style={{ fontSize: 34, marginBottom: 8 }}>📦</div>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>还没有产品</div>
          <div style={{ color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.7 }}>
            添加你的第一个产品：填写名称、一句话介绍和官网链接，<br />
            然后补充功能、定价等档案信息，就能开始创建推广活动。
          </div>
          <button className="btn btn-primary" style={{ marginTop: 14 }} onClick={() => setCreating(true)}>+ 添加产品</button>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
        {products.map((p) => (
          <div key={p.id} className="card promotion-product-card" onClick={() => onOpen(p.id)}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div className="promotion-logo" style={{ background: p.brand_color || 'var(--accent)' }}>
                {p.name.slice(0, 1)}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.name}{p.archived ? '（已归档）' : ''}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.tagline || '暂无一句话介绍'}
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 12, flexWrap: 'wrap' }}>
              <span className="badge">档案 {p.facts_confirmed ?? 0}/{p.facts_total ?? 0}</span>
              <span className="badge">素材 {p.assets_total ?? 0}</span>
              <span className="badge">活动 {p.campaigns_total ?? 0}</span>
            </div>
          </div>
        ))}
      </div>

      {creating && (
        <div className="overlay" onClick={() => setCreating(false)}>
          <div className="modal" style={{ width: 440, maxWidth: '100%' }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <h3 style={{ margin: 0 }}>添加产品</h3>
              <button className="icon-btn" onClick={() => setCreating(false)}>×</button>
            </div>
            <label className="field-label">产品名称 *</label>
            <input className="field" value={name} autoFocus placeholder="如：Showly"
              onChange={(e) => setName(e.target.value)} />
            <label className="field-label">一句话介绍</label>
            <input className="field" value={tagline} placeholder="给目标用户的价值"
              onChange={(e) => setTagline(e.target.value)} />
            <label className="field-label">官网 / 入口链接</label>
            <input className="field" value={website} placeholder="https://…"
              onChange={(e) => setWebsite(e.target.value)} />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
              <button className="btn btn-sm" onClick={() => setCreating(false)}>取消</button>
              <button className="btn btn-sm btn-primary" onClick={save} disabled={!name.trim()}>创建</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
