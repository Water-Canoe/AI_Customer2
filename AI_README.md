# AI拓客工具

## 项目定位

这是一个本地自用的 AI 拓客工作台。它不替代 MediaCrawler，而是在其之上增加任务管理、业务数据归一化、证据链、AI筛选、私信话术和跟进状态管理。

数据分三层：

1. MediaCrawler 底层原始库：默认 `D:\Dev\Projects\MediaCrawler\database\sqlite_tables.db`，只做采集保底和追溯。
2. 项目业务库：默认 `backend/runtime/ai_customer.sqlite3`，保存账号、内容、评论、线索、目标客户、证据链、AI结果和状态事件。
3. 页面视图：任务管理、数据表、总览树、AI分析和日志只是展示方式，不等于真实数据结构。

## 工作流

1. 在“设置”页配置 MediaCrawler 路径、底层 SQLite 路径、AI Base URL、API Key、模型名和 ICP 画像，并查看项目库关键字段质量与平台原始表诊断。
2. 在“任务管理”页选择四种模式之一：竞品账号采集、竞品账号爬取、找需求内容、自家账号互动；页面会按当前平台和模式显示输入要求、预计产出、字段能力、风险提示和实际执行命令预览。
3. 后端通过子进程执行 `uv run main.py` 调用 MediaCrawler，不修改 MediaCrawler 源码。
4. 采集完成后自动读取底层 SQLite，并写入项目业务库和 `raw_source_refs`。
5. 在“任务与日志”中查看本次任务产出摘要，确认内容、评论、候选竞品、线索、目标客户和需补资料账号数量。
6. 工作台顶部的“待处理队列”会汇总下一步事项：需补资料账号、竞品候选待分析、线索待筛选、目标客户待跟进和 AI 失败重试；点击队列项会直接触发受限批量补资料，或跳转到数据表筛选、AI 工作台。
7. 在“AI分析”中筛选竞品账号或目标客户，目标客户按白板状态流转：`待筛选 -> 未私信 -> 未回复 -> 已回复 -> 未成交 -> 已成交`。
7. 在“数据表”和“总览树”中查看业务数据和父子关系。

竞品账号采集的初次候选筛选规则：关键词采集内容后，作者昵称或作者主页简介任一字段命中关键词，即写入“竞品账号候选库”；昵称和简介都未命中时不进入候选库。

任务参数会按采集类型二次净化：搜索型任务只允许关键词并清空创作者/内容ID；详情任务只允许指定内容ID；账号任务只允许创作者主页/ID。搜索型任务如果关键词为空会直接失败，避免 MediaCrawler 使用自身默认关键词造成误采集。

任务管理页的执行预览调用 `POST /api/tasks/preview`，后端复用创建任务时的 `normalize_task_defaults`、`infer_crawler_type` 和 `build_command`。因此预览里的 `crawler_type`、净化后的关键词/主页/内容ID、评论开关和完整 `uv run main.py ...` 命令，就是点击“开始采集并导入”后会写入任务的实际参数。

任务管理页的“关键词”“创作者主页/ID”和“指定内容ID/链接”使用标签输入：输入一项后按回车生成独立标签，粘贴逗号或换行分隔的内容会自动拆成多个标签；这三个长输入各占一整行，提交任务时前端会把标签拼接为后端和 MediaCrawler CLI 使用的字符串参数。

账号主页简介不是所有采集模式都会带回。抖音关键词搜索当前常见结果只会写内容作者字段，`user_signature` 可能为空；需要在“数据表”的账号类库或“总览树”的账号行点击“补资料”，后端会为该账号创建 `profile_enrichment` 任务，使用 MediaCrawler creator 模式补采主页资料，并在导入时把 `dy_creator.desc` / `xhs_creator.desc` 写回账号的 `signature`。该模式下 creator 表是主页简介的权威来源；补资料任务只导入 creator 主页资料，不把同次抓到的视频改挂到补资料任务下。补资料导入成功后，系统会重新检查该账号已采集内容所属的竞品关键词；如果主页简介命中关键词，会自动补写 `account_sources` 并进入“竞品账号候选库”。

待处理队列里的“需补资料账号”支持批量动作：每次最多为 10 个缺主页简介、且有内容/评论/候选/线索证据的抖音或小红书账号创建补资料任务。后端会跳过已有 `pending/running` 补资料任务的账号，并串行执行新任务，避免同时启动多个 MediaCrawler 子进程。

平台能力矩阵通过 `CONTENT_TABLES`、`COMMENT_TABLES`、`CREATOR_TABLES` 生成，和真实导入映射保持一致。任务管理页会提前提示：抖音内容表有 `user_signature` 但可能为空，抖音/小红书可以用 creator 补资料，快手当前没有 creator 主页资料表，不能补齐主页简介和粉丝数。能力矩阵说明“理论字段和模式限制”，设置页环境诊断说明“当前原始库实际有没有行、字段是否非空”。

真实执行 MediaCrawler 的任务会记录启动时的 `raw_started_ts_ms`，归一化导入时只读取底层表中 `last_modify_ts` 或 `add_ts` 不早于该时间的原始行，避免把历史采集数据挂到当前任务证据链。`execute_crawler=false` 的模拟验证任务保留全量导入行为，方便使用 SQLite fixture 做闭环测试。

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
- `POST /api/tasks/preview`：不创建任务，只返回后端归一化后的采集类型、实际参数、净化提示和 MediaCrawler 命令预览。
- `GET /api/tasks`：查看任务列表，返回每个任务的 `outcome` 产出摘要。
- `GET /api/tasks/{id}`：查看任务详情、日志和 `outcome` 产出摘要；摘要包含内容、评论、候选竞品、线索、目标客户、原始映射、需补资料账号数量和下一步建议。
- `GET /api/workbench/actions`：返回工作台待处理队列，包含队列数量、优先级、跳转页面、目标数据表和状态筛选条件。
- `GET /api/platform-capabilities`：返回三平台在四种任务模式下的输入要求、预计产出、字段能力和已知空字段风险，用于任务创建前防错。
- `GET /api/tables/{library}`：查看内容库、评论库、竞品库、线索库和目标客户库。
- `POST /api/ai/jobs`：对竞品、线索或内容发起 AI 分析。
- `GET /api/overview/tree`：查看平台、关键词、账号、内容、客户的总览树。
- `GET /api/settings/env-check`：检查项目库、MediaCrawler 路径、底层库、AI 配置；同时返回项目库关键字段质量和按平台诊断的 MediaCrawler 原始表、行数、关键字段非空情况。
- `POST /api/settings/clear-data`：清空项目业务库和当前设置指向的 MediaCrawler SQLite 业务表，必须输入确认文本 `清空所有数据`。
- `POST /api/accounts/{account_id}/profile-enrichment`：为抖音/小红书账号创建主页资料补全任务。导入 creator 主页简介后会自动复判竞品关键词命中关系；快手当前不会创建该任务，因为 MediaCrawler SQLite store 未写入快手 creator 资料。
- `POST /api/accounts/profile-enrichment/batch`：批量创建主页资料补全任务，默认最多 10 个并串行执行；`limit` 最大 50，前端使用 10。

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
3. 看任务产出摘要和日志确认任务是否真正产生有效数据。
4. 在数据表与总览树理解线索来源。
5. 在 AI 分析工作台批量筛选并跟进。

双栏页面统一使用可拖拽 SplitPane：任务管理、AI分析、任务与日志、设置页都可以拖动中间分隔条调整左右宽度。任务管理页在模式卡片和参数表单之间显示平台能力面板，让用户先知道当前模式会调用 search、creator 还是 detail，以及主页简介、评论者简介、粉丝数这些字段是否可导入、可能为空或完全不支持；参数表单下方显示执行预览，直接展示后端最终会传给 MediaCrawler 的命令，减少误填关键词、主页或内容ID造成的误采集。任务与日志页会先显示任务产出摘要和下一步建议，再显示固定高度日志窗口；日志增加时只在窗口内部滚动，不再撑高整个页面。顶部“待处理队列”按业务动作聚合下一步事项，避免用户只看到表格而不知道该先补资料、筛竞品、筛线索还是重试 AI。总览树页面为单栏层级表，默认收起，账号名称可点击跳转主页，账号副标题显示主页简介，不再把主页链接当作描述文本；当抖音/小红书账号缺少主页简介时，总览树账号行会提供“补资料”入口。数据表页为上下布局，库选择位于表格上方，并提供状态筛选和关键词搜索；表格的“名称/内容”列会优先链接到内容页或账号主页，表头可拖拽调整列宽。设置页的 ICP 画像以字段输入表单编辑，前端保存时再组装为 `icp_profile` JSON，不再要求用户直接编辑 JSON；右侧环境状态会先显示项目库关键字段质量，再显示三平台原始表、原始行数、关键字段非空比例和平台限制，方便判断空数据到底来自路径错误、未采集、导入缺字段还是平台能力差异。

Figma 文件已创建：`https://www.figma.com/design/GGrd4r3M88ajst3oT2Y8tI`。当前账号 Starter plan 的 MCP 调用限额阻止继续写入画布，因此前端视觉规范直接落在代码中。Canva 已用于准备“用户工作流与界面信息架构”的视觉参考。

## 已知限制

- MediaCrawler 仓库许可证声明为非商业学习使用，本项目按本地自用验证处理。
- 首版只覆盖文档要求的平台：抖音、小红书、快手。
- 自动测试默认使用模拟 MediaCrawler SQLite，不会触发真实采集。
- 如果真实采集失败，应先看“任务与日志”的控制台输出，不会使用假数据兜底。
- 快手上游 SQLite store 目前没有保存 creator 主页资料，因此“补资料”只支持抖音和小红书；快手账号的主页简介不会被伪造，任务页也会提前显示该限制。
