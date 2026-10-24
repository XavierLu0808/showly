<h1 align="center">Showly</h1>

<p align="center">
  面向独立开发者和小团队的 AI 产品宣传平台<br>
  维护产品档案，组织推广活动，生成内容草稿，审核导出并记录发布效果。
</p>

## 项目介绍

Showly 将一个产品的功能、受众、价格、版本和素材集中管理，围绕首次上线、功能更新和场景教程组织推广活动。每次活动分别保存方案、内容修订、任务、审核与发布记录，方便后续修改和复用。

目标交付包括平台文案、截图卡片、公众号文章和真实操作演示视频。**当前已实现的是产品与活动管理、文字草稿、检查审核、ZIP 导出和基础效果记录。** 自动获取产品画面、卡片图片渲染、公众号排版和视频成片尚未接入活动流程。

项目基于 [Easel](https://github.com/ZJU-REAL/Easel) 开发，保留其对话、账号画像、技能与独立发布工具。产品推广业务位于 `easel/promotion/`。当前 Python 包和命令仍名为 `easel`，环境变量使用 `EASEL_*`；运行时请使用下文中的实际命令。

## 当前功能

Web 工作台的业务入口是侧栏「我的产品」。页面能力与后端接口的范围如下：

| 模块 | 当前页面支持 | 后端补充能力 |
| --- | --- | --- |
| 产品档案 | 创建与查看产品、添加事实、确认事实、创建并确认版本快照 | 编辑与归档产品，修改事实正文、来源和上线状态 |
| 素材库 | 上传截图、录屏、品牌和证明素材，查看名称、删除未被引用的记录 | 按产品和版本校验素材，保存引用与使用限制 |
| 推广活动 | 选择活动模板与受众，生成简报、角度，确认内容清单 | 保存目标、渠道、身份语气、行动引导、计划日期和目标版本等字段 |
| 内容编辑 | 生成标题和正文，编辑保存、复制文案、查看修订历史和卡片分页文本 | 保存结构化内容、素材引用及生成输入快照 |
| 检查审核 | 执行规则检查，人工审核与撤销审核 | 审核绑定修订；编辑或产品事实变化后旧审核失效；拦截错误归属和过期生成结果 |
| 导出发布 | 下载活动 ZIP，回填作品链接并标注人工确认来源 | 保存账号、发布时间、发布状态等记录 |
| 效果记录 | 录入指标名称与数值，生成 UTM 链接 | 指定指标的平台、内容、统计时间窗；JSON 批次导入与去重 |
| 任务 | 显示活动任务进度、错误和失败重试入口 | 保存输入快照；重启后识别中断任务；重试复用原输入 |

当前输出分别为小红书文案与卡片分页结构、公众号文章草稿、演示视频脚本。ZIP 以 Markdown、结构化 JSON 和已绑定的素材为主。

使用时还需注意这些实际边界：

- 页面暂不提供角度勾选、自定义方向、AI 局部改写或素材引用选择。上传素材不会自动将其绑定到内容；后端可通过 `asset_refs` 保存引用。
- 发布记录页面只填写作品链接，省略发布时间时后端记录当前时间；补录实际时间需使用接口。导出不会创建发布记录或自动提交平台。
- 指标导入目前是 JSON API，没有 CSV/Excel 文件导入页面；页面也未提供平台、时间窗和内容归属的完整录入。UTM 页面默认使用小红书来源，其他参数可通过接口指定。
- 采集、图片渲染、公众号 HTML、MP4、登录接管和脱敏尚未形成活动工作流。配置媒体服务或存在相关技能目录，并不代表这些流程已经接通。
- 活动日期尚未联动既有日历；对话页尚未自动带入当前产品与活动。取消操作目前只对排队任务生效。

目标范围见[产品功能文档](docs/product-promotion-v1.md)，具体交付与验证边界见[实施状态](docs/product-promotion-v1-status.md)。

## 本地安装与启动

### 环境要求

- Python 3.10 及以上，包含 `venv`；Git。
- Node.js 与 npm，用于构建 React 前端。仅安装 Web 时，当前安装脚本接受 Node 20.19+（20.x）、22.12+（22.x）或 23 及以上；使用 Node 24.x 可同时满足前端需求。
- 若安装 OpenClaw，脚本要求 Node 24.16+（24.x）、26.1+（26.x）或 27 及以上。
- FFmpeg 用于音视频处理，Playwright Chromium 用于浏览器登录和发布。它们的可用性不影响产品资料的保存，但会影响相关媒体或浏览器工具。

### macOS / Linux

下面安装 Web 与项目依赖，跳过 OpenClaw 的安装和初始化。预先创建虚拟环境，避免非交互运行安装器时使用系统 Python。

```bash
git clone https://github.com/XavierLu0808/showly.git
cd showly
python3 -m venv .venv
source .venv/bin/activate
bash setup.sh --skip-openclaw
easel web
```

### Windows PowerShell

```powershell
git clone https://github.com/XavierLu0808/showly.git
cd showly
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1 -SkipOpenClaw
.\.venv\Scripts\easel.exe web
```

安装器会安装 Python 依赖、构建前端并尝试准备浏览器依赖。若希望安装或复用 OpenClaw，可改用 `bash setup.sh --openclaw`；Windows 对应 `.\setup.ps1 -OpenClaw`。跳过 OpenClaw 不会自动安装 Codex、OpenCode 或其他 Agent，也不会覆盖已有的执行引擎选择。

启动后访问 **http://localhost:7860**。自定义端口使用：

```bash
easel web --port 7870
```

后续在新终端启动时，macOS / Linux 先运行 `source .venv/bin/activate`；Windows 可继续使用 `.\.venv\Scripts\easel.exe web`。命令未找到时，优先使用项目虚拟环境中的可执行文件。

当前版本面向本地单人使用，Web 进程默认监听 `0.0.0.0`，尚未提供完整的多人认证与资源隔离。通过 SSH 使用服务器时，建议转发 `7860` 端口后访问，避免直接将工作台作为公共服务开放。

## 模型与执行引擎

### 活动草稿生成

产品推广模块独立读取以下配置，与聊天页选择的 Agent 分开。可以在 Web「设置 → 模型配置 → 对话与脚本」中选择「仅聊天模式」，填写 API 地址、模型与 Key 并保存；也可编辑项目根目录 `.env`：

```dotenv
EASEL_DIRECT_API_BASE_URL=https://your-provider.example/v1
EASEL_DIRECT_API_MODEL=your-model-id
EASEL_DIRECT_API_KEY=填写真实的APIKey
```

请将示例替换为实际服务信息。接口需要兼容 OpenAI Chat Completions；Base URL 填 API 根地址，通常以 `/v1` 结尾。只有服务明确无需鉴权时，Key 才可留空。进程环境变量优先于 `.env`。

**仅配置 OpenClaw、Codex 或 OpenCode 的模型与登录，不能替代推广模块的 `EASEL_DIRECT_API_*` 配置。** `OPENAI_*` 和 `EASEL_LLM_*` 也属于其他通道。推广模块未配置直连模型，或模型调用失败时，会生成模板草稿并记录 `generation_mode=template`；模板仍需人工补充和审核。

### 对话和工具执行

在设置中选择执行引擎：

| 引擎 | 用途与依赖 |
| --- | --- |
| 仅聊天模式 | 使用直连 API 进行问答、写作和分析；没有本机命令、浏览器或技能执行工具 |
| OpenClaw | 使用已配置的 OpenClaw gateway / CLI 执行对话和工具 |
| Codex / OpenCode | 使用本机已安装并完成原生认证的 CLI；连接由项目适配器管理 |
| 通用 ACP | 连接管理员配置的本机 ACP Agent 命令 |

新安装若没有已有 Agent 配置，会使用仅聊天模式；已有配置会保留。选择其他引擎后建议新建对话。更多配置和权限说明见[Agent 底座文档](docs/agent-harnesses.md)。

视频、语音、音乐等通道按需单独配置，变量说明见 [.env.example](.env.example)。这些配置不会自动让推广模块具备媒体渲染能力。

## 完成一次文字推广活动

1. 在「我的产品」新建产品，填写名称、介绍和入口。
2. 添加功能、受众、定价或限制等事实，标记上线状态，逐项确认后点击「拍快照并确认」。未知事实可以保留待确认，不会自动成为产品承诺。
3. 创建活动，选择首次上线、功能更新或场景教程，并填写受众。页面当前固定使用小红书、公众号和短视频三类渠道。
4. 生成简报与宣传角度，确认方案以建立内容清单。重复确认不会重复创建同一角度和形式的内容。
5. 打开单项内容，生成草稿，编辑标题与正文并保存修订。卡片目前预览分页文本，视频目前展示脚本。
6. 执行检查并人工审核。审核使用活动绑定版本的事实和当前产品红线；阻断问题需先修改。规则检查是启发式辅助，需要人工核对事实和表达。
7. 返回活动页下载 ZIP，在对应官方平台自行提交，再回填作品链接，按需录入指标。

生成前必须有已确认的产品版本，生成内容草稿前必须确认方案。活动绑定版本后不会自动跟随新版本变化；更新产品时应确认新事实、创建新版本，并为该版本创建活动。

生成任务保存当时的产品、版本事实、活动配置、素材引用和基线修订。重试复用原输入，迟到的生成结果不会覆盖后来的用户编辑。历史任务若没有输入快照，需要重新创建任务。

## 既有工具与常用命令

工作台仍包含对话、账号画像、内容库、技能库及独立发布中心。它们与产品活动模块的接入程度不同。独立人工发布页已有公众号排版与 HTML 导出能力，产品活动导出尚未复用该流程；说明见[人工发布工作流](docs/manual-publishing.md)。

| 命令 | 作用 |
| --- | --- |
| `easel web --port 7860` | 启动 Web 工作台 |
| `easel chat` | 开启终端对话，可选择账号画像 |
| `easel skill <name> -i "任务" -p "画像名"` | 通过已配置的执行引擎运行指定技能 |
| `easel doctor` | 检查本机依赖、配置和安装状态 |
| `easel ping` | 实际请求当前选用的模型或 Agent，验证连通性 |
| `easel gateway start\|stop\|restart\|status\|logs` | 管理 OpenClaw gateway，仅对应 OpenClaw 路径 |

`doctor` 的检查通过不代表媒体工具或推广流程已经端到端验收。`ping` 需要有效模型配置或原生认证，会产生实际模型请求。

账号画像保存在 `profiles/<名字>/`，用于身份、受众、风格、偏好和记忆；产品事实与版本由推广模块独立保存。活动中的身份与语气字段尚未完整组合画像文件，不能假定对话画像已经自动进入活动生成。

## 数据与代码位置

| 路径 | 内容 |
| --- | --- |
| `easel/promotion/` | 产品、活动、修订、审核、任务、发布记录、指标及 `/api/promotion/*` 路由 |
| `web/app.py` | FastAPI 服务与独立路由挂载 |
| `web/frontend/src/components/promotion/` | 产品、活动和内容编辑页面 |
| `web/frontend/src/lib/promotionApi.ts` | 推广 API 请求与前端类型 |
| `easel/harnesses/` | 执行引擎适配与会话管理 |
| `profiles/` | 账号画像 |
| `skills/builtin/`、`skills/shared/` | 既有技能、工具脚本和参考资料 |
| `outputs/` | 内容文件、任务与本地运行数据 |
| `agents/` | Agent 提示词、工作区资源和底座支持文件 |
| `tests/` | 后端与前端回归测试 |
| `docs/` | 功能文档、改造方案、实施状态与运行说明 |

推广数据库默认位于 `outputs/_promotion/promotion.db`；上传素材位于 `outputs/_promotion/assets/`，活动 ZIP 位于 `outputs/_promotion/exports/`。备份时需同时保留数据库和引用文件。数据库路径可通过进程环境变量 `EASEL_PROMOTION_DB` 覆盖，素材与导出目录仍使用项目 `outputs/`。

Web 优先加载 `web/frontend/dist/` 的构建结果。修改前端后，在 `web/frontend/` 目录重新构建：

```bash
npm ci
npm run build
```

## 验证与文档

在已安装项目依赖的虚拟环境中补装 `pytest` 后，可从仓库根目录运行推广模块回归测试：

```bash
python -m pip install pytest
python -m pytest tests/test_promotion.py tests/test_promotion_review_regressions.py tests/test_promotion_snapshots.py -q
node --test tests/frontend/*.test.mjs
```

前端测试需要先在 `web/frontend/` 安装 npm 依赖。构建和 lint 在该目录执行：

```bash
npm run build
npm run lint
```

- [产品功能文档](docs/product-promotion-v1.md)：目标范围和验收标准。
- [代码改造方案](docs/product-promotion-v1-implementation.md)：数据模型、复用路径与实施要求。
- [实施状态](docs/product-promotion-v1-status.md)：当前交付、验证范围与尚缺能力。
- [Agent 底座](docs/agent-harnesses.md)：执行引擎与本机认证说明。
- [人工发布](docs/manual-publishing.md)：独立发布页的审核和导出流程。
- [技能能力地图](docs/skill-function-mapping.md)：既有技能与工具说明。
- [已知问题](docs/known-issues.md)：运行环境与功能限制。
