# 产品推广平台第一版：实施状态

日期：2026-10-23
状态：第一版已落地（本文记录实际交付范围与验证方式）

对应[产品功能文档](product-promotion-v1.md)与[代码改造方案](product-promotion-v1-implementation.md)。

## 已落地能力

| 模块 | 交付内容 | 位置 |
| --- | --- | --- |
| 数据模型 | 12 张表（Product / Version / Fact / Asset / Campaign / ContentVariant / ContentRevision / Review / GenerationJob / Publication / MetricRecord / TrackingLink），SQLite + 版本化迁移 | `easel/promotion/db.py` |
| 产品与档案 | 产品 CRUD 与归档、事实登记与确认、版本快照与确认；事实带来源与上线状态（已上线/测试中/计划中/待核实） | `easel/promotion/service.py` |
| 素材库 | 分类上传（品牌/截图/录屏/证明）、登记、删除保护（被内容引用的素材不可删）、路径限定在内容库内 | `service.py` + `router.py` |
| 推广活动 | 三类模板（首次上线/功能更新/场景教程）、活动简报、宣传角度、确认方案生成内容任务清单 | `service.py` + `generation.py` |
| 内容与修订 | 每次编辑生成新修订；修订绑定素材引用与输入快照；修订历史可查 | `service.py` |
| 内容检查 | 六类规则检查（功能事实、定价与限制、宣传依据、素材一致性、平台格式、内容完整性）+ 档案红线；error 阻断审核 | `checks.py` |
| 审核 | 人工确认绑定具体修订；内容变化/产品事实更新后旧审核自动失效（待复核） | `service.py` |
| 生成任务 | queued/running/succeeded/failed/cancelled 状态机、后台线程执行、步骤与错误摘要落库、幂等（同对象同类任务防并发）、重启恢复（中断任务标记可重试） | `jobs.py` |
| 文案生成 | LLM 可用（EASEL_DIRECT_API_*）则模型生成；未配置或失败时降级为档案模板草稿并明确标注 `generation_mode` | `generation.py` |
| 发布记录 | 人工回填（链接/时间/状态），来源固定标注 `manual-confirmation`（人工确认、未经平台自动核验）；导出仅标记已导出 | `service.py` |
| 活动导出 | 按渠道分目录的宣传包 ZIP + 发布说明；仅导出已人工审核内容 | `service.py` |
| 指标 | 手动录入、批次导入（重复批次拒绝，避免重复累计）、按指标/平台/时间窗汇总 | `service.py` |
| UTM | 按 `utm_source/medium/campaign/content` 生成追踪链接 | `service.py` |
| API | `/api/promotion/*` 独立路由（37 个端点），由 `web/app.py` 挂载，启动时建库并恢复中断任务 | `router.py` + `web/app.py` |
| 前端 | 侧栏「我的产品」入口：产品列表 → 产品概览（档案/素材/活动）→ 活动详情（简报/角度/内容清单/任务/发布/效果）→ 内容编辑器（编辑/生成/检查/审核/历史/卡片预览） | `web/frontend/src/components/promotion/` |

## 首版约定与边界

- 生成优先走直连 API；未配置时模板降级可用，媒体服务（配音、视频渲染等）缺失时能力检查明确提示，不阻断其他内容。
- 发布为人工闭环：导出不等于已发布，发布记录只能人工回填并注明来源；生成任务重试不会产生发布记录（结构上杜绝重复提交）。
- 录屏演示视频首版交付解说词与剪辑方案（脚本结构化生成与预览）；实际 MP4 渲染复用现有视频技能，按渠道验证后接入（见改造方案「后续版本」）。

## 验证

- 自动化测试：`tests/test_promotion.py`（15 项），覆盖功能文档第 6 节可自动化的验收场景：多产品隔离、缺定价不编造免费、计划功能口径、审核失效、事实更新触发待复核、任务重启恢复与幂等、导出需审核且不标记发布、人工回填来源、指标批次去重、素材删除保护。
- API 级验证：FastAPI TestClient 走通「产品 → 事实确认 → 版本确认 → 活动 → 简报/角度任务 → 确认方案 → 内容生成 → 检查 → 审核 → 发布回填 → ZIP 导出 → 指标」全链路。
- 前端：`tsc -b && vite build` 与 `oxlint` 通过。
