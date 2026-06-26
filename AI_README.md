# AI拓客工具

## 项目定位

这是一个本地自用的 AI 拓客工作台。它不替代 MediaCrawler，而是在其之上增加任务管理、业务数据归一化、证据链、AI筛选、私信话术和跟进状态管理。

数据分三层：

1. MediaCrawler 底层原始库：默认 `D:\Dev\Projects\MediaCrawler\database\sqlite_tables.db`，只做采集保底和追溯。
2. 项目业务库：默认 `backend/runtime/ai_customer.sqlite3`，保存账号、内容、评论、线索、目标客户、证据链、AI结果和状态事件。
3. 页面视图：任务管理、数据表、总览树、AI分析和日志只是展示方式，不等于真实数据结构。

## 工作流

1. 在“设置”页配置 MediaCrawler 路径、底层 SQLite 路径、AI Base URL、API Key、模型名和 ICP 画像。
2. 在“任务管理”页选择四种模式之一：竞品账号采集、竞品账号爬取、找需求内容、自家账号互动。
3. 后端通过子进程执行 `uv run main.py` 调用 MediaCrawler，不修改 MediaCrawler 源码。
4. 采集完成后自动读取底层 SQLite，并写入项目业务库和 `raw_source_refs`。
5. 在“数据表”和“总览树”中查看业务数据和父子关系。
6. 在“AI分析”中筛选竞品账号或目标客户，目标客户按白板状态流转：`待筛选 -> 未私信 -> 未回复 -> 已回复 -> 未成交 -> 已成交`。

竞品账号采集的初次候选筛选规则：关键词采集内容后，作者昵称或作者主页简介任一字段命中关键词，即写入“竞品账号候选库”；昵称和简介都未命中时不进入候选库。

任务参数会按采集类型二次净化：搜索型任务只允许关键词并清空创作者/内容ID；详情任务只允许指定内容ID；账号任务只允许创作者主页/ID。搜索型任务如果关键词为空会直接失败，避免 MediaCrawler 使用自身默认关键词造成误采集。

任务管理页的“关键词”“创作者主页/ID”和“指定内容ID/链接”使用标签输入：输入一项后按回车生成独立标签，粘贴逗号或换行分隔的内容会自动拆成多个标签；这三个长输入各占一整行，提交任务时前端会把标签拼接为后端和 MediaCrawler CLI 使用的字符串参数。

账号主页简介不是所有采集模式都会带回。抖音关键词搜索当前常见结果只会写内容作者字段，`user_signature` 可能为空；需要在“数据表”的账号类库中点击“补资料”，后端会为该账号创建 `profile_enrichment` 任务，使用 MediaCrawler creator 模式补采主页资料，并在导入时把 `dy_creator.desc` / `xhs_creator.desc` 写回账号的 `signature`。该模式下 creator 表是主页简介的权威来源，随账号视频一起导入的内容作者字段不会反向覆盖主页简介。

## 启动方式

后端：

```powershell
# 进入后端目录，后续命令都在这里执行
Set-Location "D:\Dev\Projects\Web_Project\AI_Customer\backend"

# 创建本项目专用 Python 虚拟环境，避免污染全局环境
python -m venv .venv

# 激活虚拟环境，让 pip 和 python 使用本项目依赖
.\.venv\Scripts\Activate.ps1

# 安装 FastAPI、测试工具等后端依赖
python -m pip install -r requirements.txt

# 启动后端 API 服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端：

```powershell
# 进入前端目录，后续命令都在这里执行
Set-Location "D:\Dev\Projects\Web_Project\AI_Customer\frontend"

# 安装 Vue、Element Plus、Vite 等前端依赖
npm install

# 启动前端开发服务器
npm run dev
```

打开 `http://127.0.0.1:5173` 使用工作台。

## 关键接口

- `POST /api/tasks`：创建采集任务并后台执行。
- `GET /api/tasks`：查看任务列表。
- `GET /api/tasks/{id}`：查看任务详情和日志。
- `GET /api/tables/{library}`：查看内容库、评论库、竞品库、线索库和目标客户库。
- `POST /api/ai/jobs`：对竞品、线索或内容发起 AI 分析。
- `GET /api/overview/tree`：查看平台、关键词、账号、内容、客户的总览树。
- `GET /api/settings/env-check`：检查项目库、MediaCrawler 路径、底层库和 AI 配置。
- `POST /api/settings/clear-data`：清空项目业务库和当前设置指向的 MediaCrawler SQLite 业务表，必须输入确认文本 `清空所有数据`。
- `POST /api/accounts/{account_id}/profile-enrichment`：为抖音/小红书账号创建主页资料补全任务。快手当前不会创建该任务，因为 MediaCrawler SQLite store 未写入快手 creator 资料。

## 删除规则

- 普通编辑只改项目业务库，不改 MediaCrawler 原始数据。
- 硬删除必须同时删除项目库和 MediaCrawler 底层库映射数据。
- 如果缺少 `raw_source_refs` 映射，后端会停止并返回错误，不会静默跳过底层删除。
- 目标客户普通删除优先隐藏并写入状态事件；危险硬删除需要明确确认。
- 多来源客户通过 `lead_sources.active` 处理引用计数，仍有其它来源时不会直接删除账号本体。
- 设置页的“清空所有数据”用于重新开始项目：会清空项目库中的任务、日志、账号、内容、评论、线索、证据链、AI结果和删除审计，并清空 MediaCrawler SQLite 的所有非系统业务表；数据库文件和设置项会保留，任务编号会重置为 `0001`。

## 设计说明

用户界面按真实使用顺序组织：

1. 设置环境和 ICP。
2. 创建采集任务。
3. 看日志确认任务完成。
4. 在数据表与总览树理解线索来源。
5. 在 AI 分析工作台批量筛选并跟进。

双栏页面统一使用可拖拽 SplitPane：任务管理、AI分析、任务与日志、设置页都可以拖动中间分隔条调整左右宽度。任务与日志页的日志窗口保持固定高度，日志增加时只在窗口内部滚动，不再撑高整个页面。总览树页面为单栏层级表，默认收起，账号名称可点击跳转主页，账号副标题显示主页简介，不再把主页链接当作描述文本。数据表页为上下布局，库选择位于表格上方，表格的“名称/内容”列会优先链接到内容页或账号主页，表头可拖拽调整列宽。设置页的 ICP 画像以字段输入表单编辑，前端保存时再组装为 `icp_profile` JSON，不再要求用户直接编辑 JSON。

Figma 文件已创建：`https://www.figma.com/design/GGrd4r3M88ajst3oT2Y8tI`。当前账号 Starter plan 的 MCP 调用限额阻止继续写入画布，因此前端视觉规范直接落在代码中。Canva 已用于准备“用户工作流与界面信息架构”的视觉参考。

## 已知限制

- MediaCrawler 仓库许可证声明为非商业学习使用，本项目按本地自用验证处理。
- 首版只覆盖文档要求的平台：抖音、小红书、快手。
- 自动测试默认使用模拟 MediaCrawler SQLite，不会触发真实采集。
- 如果真实采集失败，应先看“任务与日志”的控制台输出，不会使用假数据兜底。
- 快手上游 SQLite store 目前没有保存 creator 主页资料，因此“补资料”只支持抖音和小红书；快手账号的主页简介不会被伪造。
