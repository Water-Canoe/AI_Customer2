# AI_Customer 与 Blind_Watermark 共性能力统一规范

> 文档用途：直接交给 Blind_Watermark 项目的 Agent，作为共性基础设施改造、测试和验收依据。
>
> 核对基线：AI_Customer `1.2.9 / cac3e57`，Blind_Watermark `1.2.1 / efc8456`，核对日期 `2026-07-19`。

## 1. 结论与执行边界

两个项目应统一“用户如何使用、任务如何运行、授权如何校验、版本如何交付和更新”，不应为了目录看起来一样而强迫 PyInstaller 与 Nuitka 使用完全相同的内部文件布局。

Blind_Watermark 改造时遵守以下原则：

1. AI_Customer 的现行行为是共性能力基线，优先移植已经通过测试的设备身份、签名租约、统一运行队列、稳定启动器和全局设置设计。
2. Blind_Watermark 已经更强的实现必须保留：CloakBrowser 对象在所属专用线程创建和使用、Program 候选版本健康检查、整目录切换失败回滚、独立对象存储桶。
3. 统一的是协议和行为，不允许 Blind_Watermark 在运行时导入 AI_Customer 源码目录。需要复用的模块应复制后按产品常量改名，并在 Blind_Watermark 仓库独立测试。
4. 不修改盲水印、前景分割、相似度判断、证据报告和各平台采集业务算法；本次只统一共性基础设施。
5. 不移植 AI_Customer 当前已知缺口：不得让前端硬编码任务是否可取消/重试，不得保留无法协作取消的长任务，不得再叠加第二套互不知情的浏览器锁。
6. 不增加旧版兼容层。数据库结构变化使用有序迁移；旧设备授权由管理员撤销后重新激活。

## 2. 普通用户看到的统一产品模型

两个项目对非技术用户必须呈现相同的使用方式：

- 只存在一个稳定入口 EXE。用户不得直接运行内部业务 EXE。
- 左侧栏提供顶级“运行中心”，展示全产品排队、运行和历史任务。
- 左侧栏最下方固定为“全局设置”，入口旁显示授权指示灯。
- “全局设置”统一承载“版本与更新、授权与设备、平台登录、数据保护”。页面主区域独立纵向滚动。
- 业务页面只保留业务参数，不再各自放授权码、检查更新或平台登录入口。
- 页面显示当前 Program 版本、Environment 版本、数据库 schema 版本和是否为打包环境。
- 打开：双击稳定入口 EXE。重复双击只打开已经运行的工作台，不启动第二个服务。
- 关闭：点击“安全退出”，由后端生命周期依次停止调度器、运行队列和浏览器，再退出服务。
- 更新：点击“检查更新”或启动时发现更新后，由用户确认并查看真实进度；业务进程不能自行覆盖自己。

Blind_Watermark 的水印参数、店铺密钥留在水印设置；巡检阈值、抓取数量、Webhook 留在巡检设置。它们不是全局基础设施，不能为了“统一”全部搬到全局设置。

## 3. 开发环境与客户交付环境

两项目统一为“源码开发环境”和“便携交付环境”两套寻径，不要求开发者把依赖手工复制到仓库根目录 `runtime/`。

| 能力 | 开发环境 | 便携交付环境 |
| --- | --- | --- |
| Python 依赖 | 项目虚拟环境 | Program/Environment 解压后的运行库 |
| 前端 | `frontend/node_modules` + Vite | Program 内正式构建静态文件 |
| CloakBrowser | 本机已安装缓存，由环境检查确认 | Environment 内固定浏览器目录 |
| U²-Net 等大模型 | 源码配置的本机模型目录 | Environment 内模型目录 |
| 业务数据库 | 项目 `data/` | 客户解压目录 `data/` |
| 登录 Profile | 项目 `data/` 下专用目录 | 客户解压目录 `data/` 下专用目录 |
| 构建产物 | `output/`，仅作中间文件 | `deliverables/<Program版本>/`，唯一客户交付目录 |

便携目录的外部行为统一为：

```text
<产品目录>/
├─ <Product>.exe                 # 稳定入口，不由远程更新覆盖
├─ <Product_App>.exe 或 program/ # 可更新业务程序，内部布局可因打包器不同
├─ runtime/ 或 environment/      # Environment ZIP 提供的固定依赖
├─ data/                         # 数据库、素材、结果、Profile、备份、日志
├─ updates/                      # 更新临时文件和事务记录
└─ release-manifest.json         # 当前 Program 清单
```

客户首次使用只执行三步：解压 Program ZIP、把 Environment ZIP 解压到同一目录并合并、双击稳定入口。Environment ZIP 由交付人员手动提供；远程更新默认只发布 Program ZIP。Program 要求更高 Environment 版本时，启动器应明确提示重新取得对应 Environment ZIP，不在后台下载数 GB 环境包。

## 4. 设备身份与授权统一规范

### 4.1 设备身份

Blind_Watermark 必须移植 AI_Customer 的 Windows DPAPI 机器身份机制，禁止继续使用 SQLite 中首次生成的 UUID 作为可信设备码。

- 在 `%PROGRAMDATA%\Blind_Watermark\device_identity.bin` 保存由 `CRYPTPROTECT_LOCAL_MACHINE` 保护的 32 字节随机密钥。
- 身份文件使用临时文件加 `os.replace` 原子写入。
- 每次进程启动都解密密钥，并用 `SHA-256(产品命名空间 + 密钥)` 生成 128 位展示码。
- 展示格式统一为 `AI-CUS-XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX`，以兼容现有 Sealos 设备字段和管理页。
- Blind_Watermark 使用自己的命名空间，例如 `Blind_Watermark/device/v1\0`；两产品机制一致，但当前不要求同一电脑在两个产品中显示完全相同的设备码，避免一个产品删除身份文件导致另一个产品同时换绑。
- 数据库 `settings.device_code` 只作显示缓存。每次启动都与机器码比较；不一致时覆盖缓存、清除旧租约和设备统计，但保留授权码。
- 身份文件缺失、损坏或无法解密时生成新身份，并要求重新激活；不得接受复制来的数据库设备码。
- Program ZIP、Environment ZIP、业务备份和远程更新均不得包含该身份文件。
- 不读取 CPU、主板或硬盘序列号，不提供弱硬件指纹兜底，不支持非 Windows 回退。

### 4.2 一个授权码与权益划分

Blind_Watermark 本地只保存一个产品授权码，通过签名租约携带权益：

- `watermark`：创建批次、嵌入、提取、对比和生成水印业务新结果。
- `inspection`：创建或启动巡检、自动分析和生成巡检报告。

已有数据的查看、下载、导出、备份、取消、删除和清空不因授权到期而禁用。创建和执行受控任务必须双重校验：路由创建时校验一次，运行队列真正执行时再校验一次。

### 4.3 签名租约与离线规则

- 客户端调用 Sealos `activate` 或 `renew`，服务端返回 `leaseText + signature`。
- 租约由独立的 Ed25519 授权私钥签名；客户端只嵌入公钥。
- 租约字段至少包含 `version、licenseId、deviceId、entitlements、issuedAt、expiresAt`。
- 租约最长 72 小时；客户端接受的上限不得超过 73 小时，用于容忍边界时间。
- 本地租约签发超过 6 小时后，下一次受控任务尝试联网续租。
- 断网时只允许继续使用当前设备尚未过期且签名有效的租约。
- 联网返回停用、过期、撤销、超设备数或其它拒绝时，立即清除旧租约，不得继续离线放行。
- 签名、设备码、时间、权益任一不匹配均视为未授权。
- 生产版授权服务地址内置在产品配置中，不再允许前端编辑。开发测试只能通过明确环境变量覆盖。

### 4.4 启动顺序

FastAPI 生命周期固定为：

```mermaid
flowchart LR
    A[初始化数据库与迁移] --> B[读取 DPAPI 机器身份]
    B --> C[联网激活或校验本地签名租约]
    C --> D[恢复统一运行队列]
    D --> E[启动队列]
    E --> F[启动定时调度器]
    F --> G[健康检查就绪]
```

未授权或授权服务器不可达且无有效租约时，应用仍可进入页面管理授权和查看历史数据，但不能创建或执行受控任务。授权失败不得阻止安全退出、恢复备份或查看错误日志。

## 5. Sealos 授权后端统一规范

Blind_Watermark 后端路由语义与 AI_Customer 对齐，产品前缀保持独立：

```text
POST /blind-watermark/license/activate
POST /blind-watermark/license/renew
POST /blind-watermark/update/check
POST /blind-watermark/update/admin/upload-url
POST /blind-watermark/update/admin/releases
PUT  /blind-watermark/update/admin/release-status
GET  /blind-watermark/update/admin/releases
```

授权请求统一使用：

```json
{
  "licenseCode": "授权码",
  "deviceId": "机器设备码",
  "deviceName": "Windows 主机名",
  "appVersion": "1.2.2"
}
```

授权响应 `data` 至少提供：

```json
{
  "permission": true,
  "reason": "LEASE_RENEWED",
  "leaseText": "紧凑 JSON 原文",
  "signature": "Base64 Ed25519 签名",
  "maxDevices": 3,
  "activeDeviceCount": 1
}
```

服务端必须遵守：

- 继续使用 `licenseId + deviceId` 唯一约束。
- 占用设备名额、检查上限和写入设备记录必须在同一事务中完成，避免并发超卖。
- 授权记录维护状态、到期时间、最大设备数和权益列表。
- 设备记录明确区分启用与撤销；撤销释放名额，恢复前重新检查名额。
- 管理页支持授权码搜索、新增/修改、权益配置、有效期、设备上限、设备列表、撤销、恢复和最近校验信息；危险操作需要确认。
- 授权租约签名密钥与程序更新签名密钥必须分开，私钥均不得进入客户端、仓库或 Program ZIP。
- Blind_Watermark 继续使用独立存储桶 `0lgzvp7r-blind-watermark` 和对象前缀 `releases/blind-watermark/`，不得回退到 AI_Customer 存储桶。
- Access Key、Secret Key 和管理 Token 只保存在 Sealos 环境变量或发布机安全位置，客户端只接收短期下载 URL。

## 6. CloakBrowser、Playwright 与登录态统一规范

### 6.1 浏览器内核

- 正式业务统一使用 CloakBrowser 的 Playwright 兼容接口。
- Playwright 可以作为 API、Locator、异常类型和驱动依赖存在，但生产业务不得直接调用系统 Chromium 或 `playwright.chromium.launch` 绕开 CloakBrowser。
- 打包版通过 `CLOAKBROWSER_BINARY_PATH` 指向 Environment ZIP 内的固定 `chrome.exe`，并关闭运行时自动更新。
- 环境缺失或损坏时直接提示重新解压匹配的 Environment ZIP，不在业务任务中临时下载浏览器，也不切换普通 Playwright 兜底。
- 构建时确认 CloakBrowser wrapper 版本、固定 Chromium 版本和真实二进制一致。

### 6.2 统一浏览器资源

所有浏览器动作，包括手动登录窗口，都必须进入同一个全局 `browser` 资源，容量固定为 1。Blind_Watermark 现有“每个平台专用线程”继续保留，但专用线程只负责保证 Playwright 同步对象不跨线程；它不能绕过全局运行队列并与另一个平台任务并发占用浏览器。

浏览器任务流程为：运行队列取得 `browser` 资源 → 在对应平台专用线程创建/复用上下文 → 执行业务 → 在同一线程关闭或保留上下文 → 释放资源。

### 6.3 Profile 规则

- 每个平台使用独立持久化 Profile，路径只保存在本机 `data/`，不由 API 返回绝对路径。
- Blind_Watermark 当前每个平台只有一个运营登录态，不为未来可能出现的多账号提前新增账号中心。
- 若以后确实增加多账号，路径扩展为“平台 + account_id + 登录类型”，不能让不同账号复用一个 Profile。
- 用户站登录与创作者站登录不得混用。Blind_Watermark 当前只使用电商用户站，不创建无业务用途的创作者登录态。
- Profile 不进入 Program、Environment、备份和远程更新，也不随删除业务记录而隐式删除。

### 6.4 自动化行为

- 扫码或人工登录窗口默认可见并最大化。
- 生产动作优先使用 Playwright Locator；点击前重新定位和校验可见文本，避免网络重排后点错位置。
- 不使用固定屏幕坐标完成业务点击。
- 不需要浏览器下载的任务设置 `accept_downloads=False`。
- 网络等待使用阶段性超时和可观察进度，不以一次 `DOMContentLoaded` 超时直接判定整项任务失败。
- 所有轮询、页面等待、任务间隔和批量循环都必须检查取消回调。
- 用户手动关闭浏览器时，任务应进入 `interrupted` 或 `needs_login`，释放资源并继续调度后续任务，不能让 worker 永久卡死。

### 6.5 关闭与残留进程

- 正常退出先停止新任务认领，再请求运行中任务协作取消，最后回到创建线程关闭上下文。
- 只有正常关闭超时后，才允许按“本产品 Profile 根目录出现在命令行中”精确终止残留 `chrome.exe`。
- 禁止按进程名批量终止所有 Chrome、Python 或 Node 进程。

## 7. 统一运行队列与定时自动化

### 7.1 数据职责

Blind_Watermark 新增全局持久化 `runtime_jobs`，业务表继续保存业务详情：

- `inspection_jobs` 保存平台、关键词、执行快照、采集统计、分析结果和业务阶段。
- `runtime_jobs` 只保存“谁在何时使用哪类资源执行哪个业务实体”。
- 调度器只创建业务运行和 `runtime_jobs`，不得直接执行浏览器、分析或报告代码。

`runtime_jobs` 最少包含：

```text
id, kind, entity_id, resource, payload, status, priority,
attempt, max_attempts, cancel_requested, error, result,
lease_token, heartbeat_at, started_at, finished_at,
created_at, updated_at
```

状态统一为 `queued / running / succeeded / failed / cancelled / interrupted`。Blind_Watermark 的 `needs_login` 和 `success_with_warning` 继续保留在业务任务状态，运行队列通过 `result.domain_status` 暴露，不扩大全局状态集合。

同一 `kind + entity_id` 只允许一个 `queued/running` 任务。认领顺序为优先级降序，再按创建时间和 ID 正序，保证同优先级 FIFO。

### 7.2 资源与并发

Blind_Watermark 初始只建立实际需要的资源：

| 资源 | 并发 | 承载任务 |
| --- | ---: | --- |
| `browser` | 1 | 平台登录、关键词巡检、需要浏览器的详情采集 |
| `image` | 1 | 大批量嵌入、提取、主体分割和相似度分析 |
| `default` | 2 | 报告、轻量文件处理及其它不争用浏览器/模型的长任务 |

快速 CRUD 和单个轻量查询仍同步执行，不把所有接口机械地塞进队列。只有耗时、可取消、会争用浏览器/CPU/模型或需要跨重启记录的任务进入 `runtime_jobs`。

### 7.3 取消、重试和删除

- 排队任务取消后立即变为 `cancelled`，并同步业务表。
- 运行任务设置 `cancel_requested=1`，业务循环、等待和阶段边界必须协作检查。
- 浏览器任务取消时在浏览器专用线程关闭当前页面或上下文，使长等待立即返回。
- 重试前先重置对应业务状态；有外部副作用的任务不得未经确认自动重放。
- 后端在每条任务中返回 `can_cancel、can_retry、can_delete`，前端按能力显示按钮，不维护任务类型黑名单。
- 运行或排队任务不能删除；历史任务删除只删运行记录，业务数据由业务页面管理。

### 7.4 重启恢复

- 启动时检查 `running` 任务的心跳和任务类型。
- 纯分析、报告等可安全恢复任务可以重新入队。
- 浏览器操作、发布、外部写入等可能产生副作用的任务统一标记 `interrupted`，由用户确认后重试。
- 恢复不得重复执行已经保存成功的阶段；业务快照和阶段结果必须可识别。
- 关闭时停止认领新任务，等待协作取消；超时任务落为 `interrupted`，不得在数据库永久保持 `running`。

### 7.5 定时计划

- 计划修改后不改写已创建运行；每次运行保存不可变配置快照。
- 同一触发时刻按计划排序依次入队。
- Blind_Watermark 保留“当天晚于计划时间启动时补建一次”的业务规则；AI_Customer 保留“软件未运行时直接跳过”的规则。两者只统一调度架构，不强迫统一漏跑语义。
- 停止父运行时必须取消当前子任务，并阻止创建后续子任务。
- 局部分析或报告失败可继续使用 Blind_Watermark 现有 `success_with_warning` 业务状态。

```mermaid
flowchart LR
    A[定时器或手动入口] --> B[保存业务运行与配置快照]
    B --> C[创建 runtime_job]
    C --> D{资源是否空闲}
    D -- 否 --> C
    D -- 是 --> E[原子认领并写心跳]
    E --> F[运行前再次校验权益]
    F --> G[执行业务并保存进度]
    G --> H[完成/失败/取消/中断]
```

### 7.6 运行中心

顶级“运行中心”至少支持：

- 资源占用卡片。
- 状态、任务类型筛选和分页。
- 任务名称、关联业务 ID、资源、尝试次数、时间、实时进度和错误摘要。
- 由后端能力控制的取消、重试、删除。
- 点击任务跳转到对应业务详情。

维护操作、备份、恢复、清空和更新只以 `runtime_jobs` 的 `queued/running` 为统一阻塞依据；不得让业务表中没有真正入队的旧 `pending` 记录永久阻止维护。

## 8. 应用生命周期与健康检查

- `/api/health` 固定返回 `status、product、version、environment_version、schema_version、packaged`。
- 稳定启动器只在健康接口确认 `product` 和 `packaged=true` 后打开浏览器。
- 重复启动时只能打开通过产品身份校验的本机工作台，不能因为端口相同误连其它项目开发服务。
- 打包版使用产品级单实例锁；开发环境不抢占打包版锁。
- “安全退出”通过后端 API 触发 Uvicorn 正常生命周期，不依赖用户关闭一个不明确的命令行窗口。
- 日志统一写入 `data/logs/app.log`，单文件 5 MB，保留三个轮转文件。
- 启动失败对普通用户显示短错误和日志位置；完整堆栈只进日志。
- 数据库迁移、设备身份、授权启动校验、队列恢复任一步失败都不得提前打开浏览器页面。

## 9. Program / Environment 双 ZIP 构建规范

### 9.1 交付边界

Program ZIP 保存经常变化且允许远程更新的内容：

- 稳定入口 EXE（仅首次/手动交付，远程更新清单不得覆盖）。
- 业务 EXE、后端模块和前端正式构建。
- Program 发布清单和客户说明。

Environment ZIP 保存体积大且低频变化的内容：

- CloakBrowser 固定浏览器。
- Playwright 驱动等独立运行环境。
- U²-Net ONNX 等低频大模型。
- 其它可独立验证且无需每个 Program 版本重复下载的运行依赖。

Blind_Watermark 当前嵌入 Program 的 U²-Net 模型应迁入 Environment；Python wrapper 若必须随 Nuitka 业务程序编译可以继续留在 Program，不能为了目录形式统一破坏可运行性。

### 9.2 构建规则

- Program 版本、Environment 版本、schema 版本和稳定启动器版本分别管理。
- 一个源码版本只有一个可信版本源；构建时校验前端版本一致。
- 构建前运行前端测试/构建和后端测试。
- 构建脚本只校验依赖，不在构建过程中隐式升级或安装依赖。
- 每次使用唯一中间目录；旧交付物不可覆盖，同版本已存在必须提升版本。
- 客户交付物只进入 `deliverables/<Program版本>/`；`output/`、`build/`、`dist/` 均为中间产物。
- Program/Environment ZIP 都生成 SHA-256 文件。
- Program 不包含数据库、业务图片、日志、备份、Profile、更新临时文件、源码测试和开发缓存。
- Environment 不包含业务数据库和 Profile。
- Environment manifest 列出版本和最小必需文件，启动时先校验再运行。
- 没有明确打包要求时，日常代码修改只运行测试和正式前端构建，不生成交付 ZIP。

内部目录允许保留 Blind_Watermark 的 Nuitka `program/` 与 `environment/` 结构；客户操作、ZIP 边界、清单安全规则和命名语义必须与 AI_Customer 一致。

## 10. 远程更新与发布规范

### 10.1 客户端检查协议

Blind_Watermark 更新检查请求统一为：

```json
{
  "licenseCode": "授权码",
  "deviceId": "机器设备码",
  "currentVersion": "1.2.1",
  "environmentVersion": "1.0.0",
  "updaterVersion": "1.0.0",
  "channel": "stable",
  "platform": "windows",
  "arch": "x64"
}
```

服务端先验证授权和设备，再按通道、平台、架构、灰度比例、Program 版本和 Environment 要求返回更新。字段命名不得继续在 `deviceId/deviceCode`、`currentVersion/programVersion` 之间分叉。

### 10.2 更新交互

- 启动时可以静默“检查”，但发现新版本后必须弹出版本、大小、说明和是否强制，并由用户确认下载。
- 全局设置提供“检查更新”。点击后先确认全局队列空闲，再安全关闭业务进程，由稳定启动器接管。
- 显示真实下载字节、校验、解压、安装和启动进度。
- 普通更新失败时恢复当前版本并显示错误；强制更新失败时停止启动，不能绕过。
- 当前已是最新版时给出明确反馈并重新打开工作台。

### 10.3 更新安全

- 更新清单使用独立 Ed25519 私钥签名，客户端嵌入公钥。
- 校验产品、版本、平台、架构、最小启动器、Environment 版本、包大小、SHA-256 和 HTTPS 下载 URL。
- ZIP 拒绝绝对路径、`..`、符号链接、重复文件、超文件数和超解压体积。
- 只允许替换 Program 清单声明的文件；不得覆盖 `data/` 和 Environment。
- 更新必须有事务记录和回滚。Blind_Watermark 保留现有候选目录健康检查和整目录交换；这比逐文件覆盖更适合 Nuitka，不需要降级为 AI_Customer 的文件级实现。
- 稳定入口本身不远程覆盖。新 Program 要求更高启动器时，提示手动交付完整 Program ZIP。
- 更新完成后重新校验 Program 清单和健康接口，再向用户打开页面。

### 10.4 发布端

发布脚本统一执行：

1. 自动选择或显式指定已完成的 Program ZIP。
2. 本地验证 Program ZIP、版本、schema 和 Environment 要求。
3. 生成紧凑 UTF-8 更新清单并用仓库外私钥签名。
4. 用内置公钥反向验证签名匹配。
5. 使用管理员 Token 请求短期上传 URL。
6. 流式上传同一个已验证 Program ZIP。
7. 登记 `manifestText + signature + notes`。
8. 显式启用发布，设置 `mandatory、rolloutPercent、channel`。
9. 查询发布状态确认生效。

对象存储发布物不可覆盖。同版本同平台同架构已存在且哈希不同，必须发布新版本；哈希相同可以复用已登记对象，不重复上传。测试发布按用户现行习惯默认 `rolloutPercent=100`；正式生产是否灰度由发布命令显式指定。

## 11. 数据保护与迁移规范

- SQLite 连接启用 WAL、30 秒 busy timeout、外键和 `synchronous=NORMAL`。
- 正式发布后所有 schema 变化使用有序迁移和 `PRAGMA user_version`；已发布迁移不得回写修改。
- 迁移前对有业务数据的旧库使用 SQLite Online Backup API 创建安全备份。
- 手动备份使用清单记录数据库及业务文件大小、SHA-256 和目录树摘要。
- 备份包含业务数据库、明确登记的图片、报告和证据文件；不包含 Profile、浏览器、模型、缓存、日志和更新临时文件。
- 恢复前先完整校验备份，再自动备份当前状态，恢复数据库并精确同步业务文件，最后重新运行迁移。
- 备份、恢复、清空和更新共用维护窗口，并检查统一运行队列空闲。
- “清空业务记录”不得删除历史备份，也不得删除 `%PROGRAMDATA%` 设备身份。
- 备份列表分页展示，支持恢复和单个确认删除；禁止提供模糊路径删除接口。

## 12. API、前端和错误处理规范

- 路由只负责请求模型、授权、错误码和入队；业务执行放在 service。
- 不继续把新业务堆进 Blind_Watermark 当前的大型 `main.py`；本次新增设备、运行队列和更新共性能力各自放入现有或最小新 service，避免顺便重写全部路由。
- API 错误统一返回可直接展示的中文 `detail`，前端不得只显示 `Failed to fetch`。
- 网络不可达、授权拒绝、需要登录、浏览器关闭、环境缺失、任务取消和业务失败使用不同错误原因。
- 前端共享 API 客户端负责超时、JSON 错误解析和连接失败提示。
- 全局自动刷新按页面裁剪；运行中可 3 秒刷新，空闲可 12 秒刷新。编辑表单时不得被自动刷新覆盖草稿。
- 列表默认服务端分页；不得一次加载全部任务、备份、报告或账号记录。
- 所有危险操作二次确认；禁用按钮必须说明原因。

## 13. Blind_Watermark 改造顺序

Agent 必须按以下小阶段实施并分别提交中文 Git commit，不能一次性重写整个项目。

### 阶段 A：机器身份与签名授权

主要文件：

- 新增 `backend/app/device_identity.py`。
- 修改 `backend/app/license_service.py`、`backend/app/main.py`、`packaging/blind_watermark_launcher.py`。
- 修改 Blind_Watermark 的 Sealos 授权路由和授权管理页。
- 新增 DPAPI、设备变化、签名租约、离线租约和启动顺序测试。

完成标准：复制整个项目目录到另一台 Windows 后设备码变化，复制租约失效；同机删除项目目录再安装设备码不变。

### 阶段 B：统一运行队列与取消机制

主要文件：

- 为数据库增加下一版本迁移和 `runtime_jobs`。
- 新增最小 `runtime_queue.py` service 与 `/api/runtime/jobs` 路由。
- 将 `inspection_service.py` 的 worker 改为业务执行器，由统一队列认领。
- 为等待、循环、分析和报告增加协作取消。
- 新增顶级运行中心页面。

完成标准：排队任务立即取消，运行任务在可控时间内停止，后续任务继续执行；重启后数据库不存在永久 `running`。

### 阶段 C：浏览器资源与全局设置

主要文件：

- 保留并整理 `inspection_crawler.py` 的平台专用线程。
- 手动登录和巡检统一取得 `browser` 资源。
- 将授权、更新和平台登录收口到侧栏底部“全局设置”。
- 增加授权灯、版本信息、安全退出和全局页面滚动。

完成标准：登录窗口打开时巡检任务等待；巡检运行时登录按钮显示等待或禁用原因；退出后无本项目 Profile 对应的残留 Chrome。

### 阶段 D：双 ZIP 与更新协议收口

主要文件：

- 修改 `script/build-release.ps1`、`script/package_release.py`、`script/publish-release.ps1`。
- 修改 `packaging/blind_watermark_launcher.py`、`backend/app/update_service.py`。
- U²-Net 模型迁入 Environment，Program 远程更新不再重复携带。
- 更新请求字段、Sealos 路由、对象键和发布状态接口与本规范一致。

完成标准：Program 和 Environment 合并后可离线启动；Program 更新不覆盖数据和环境；损坏、篡改或健康检查失败会恢复旧 Program。

### 阶段 E：数据保护、回归与文档

主要文件：

- 让维护窗口只依据统一运行队列判断空闲。
- 补齐备份清单、精确恢复、分页和单个删除。
- 同步根目录 `AI_README.md` 和客户说明。
- 运行完整后端测试、前端测试、生产构建和真实便携目录 smoke test。

没有用户明确要求时，到此只修改源码、测试和文档，不生成或上传新交付版本。

## 14. 最小验收清单

### 设备与授权

- [ ] 同一 Windows 重复启动设备码不变。
- [ ] 复制数据库和项目目录到另一台电脑后设备码不同，旧租约不可用。
- [ ] 身份文件损坏后生成新设备码、清除租约、保留授权码。
- [ ] 签名被篡改、设备不匹配、权益缺失和租约过期均拒绝。
- [ ] 有效租约断网可用，72 小时后拒绝。
- [ ] 在线撤销或停用立即清除本地租约。

### 任务与浏览器

- [ ] 同一时间最多一个浏览器动作。
- [ ] 登录、任务、关闭均在正确浏览器线程执行。
- [ ] 排队/运行任务可取消，取消后资源释放且队列继续。
- [ ] 重启恢复策略不会自动重放有外部副作用的动作。
- [ ] 运行中心分页、筛选、进度和操作能力来自后端。
- [ ] 缺少 Environment 浏览器时直接失败，运行时不下载、不切换普通 Chromium。

### 打包与更新

- [ ] Program ZIP 与 Environment ZIP 合并后可在干净 Windows 启动。
- [ ] Program 不含数据库、Profile、日志、备份和 U²-Net 大模型。
- [ ] Environment 不含业务数据库和 Profile。
- [ ] 更新清单、签名、ZIP 路径、大小和 SHA-256 全部校验。
- [ ] 更新不会覆盖 `data/`、Environment 和稳定入口。
- [ ] 候选健康检查失败可恢复旧 Program 和更新前数据库。
- [ ] 同版本发布物禁止覆盖，Blind_Watermark 对象仍在独立桶。

### 生命周期与前端

- [ ] 授权启动检查发生在队列和调度器启动之前。
- [ ] 重复双击只打开已运行的正确产品页面。
- [ ] 检查更新和安全退出都会先确认队列空闲并正常关闭浏览器。
- [ ] 全局设置包含更新、授权设备、平台登录和数据保护，页面可滚动。
- [ ] 运行中心是顶级页面，不属于水印或巡检工作台。
- [ ] 前端失败展示具体中文原因，不只显示 `Failed to fetch`。

## 15. 明确禁止事项

- 禁止继续信任 SQLite 内可复制的设备码。
- 禁止将授权服务 URL、对象存储密钥或签名私钥暴露给前端。
- 禁止只在前端控制授权，后端任务执行前必须复核。
- 禁止浏览器业务绕过统一队列单独启动 CloakBrowser。
- 禁止为了取消任务直接结束所有 Chrome/Python 进程。
- 禁止在更新中覆盖 `data/`、Environment 或稳定入口。
- 禁止同版本覆盖旧发布物。
- 禁止在没有明确要求时每次代码修改后自动打包。
- 禁止为了统一目录名称而删除 Blind_Watermark 已验证的候选健康检查和整目录回滚。
