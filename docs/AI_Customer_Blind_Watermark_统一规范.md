# AI_Customer 与 Blind_Watermark 共性能力统一规范

核对日期：2026-10-11。适用于双方已确认的源码优化；不代表已经构建、发布或真实升级验收。

本规范取代 2026-07-19 的旧方案。两项目各自保留仓库、业务模型和测试，不在运行时导入对方工程，不新建共享框架。双方直接用户请求已授权实施，并确认 Core 单包首装；当前测试数据可重建，但更新器不会因此自动清库。

## 通用后端接入（2026-10-11）

- 两项目共用 `MINI_BACKEND_URL`，默认 `https://puxaxxvlzefd.sealosbja.site`；授权、更新、入口更新器和发行脚本分别派生 `/api/v1/products/ai-customer`、`/api/v1/products/blind-watermark`，移除旧分散地址覆盖和 SSH 读取旧管理令牌。
- 管理凭据统一为显式 `MINI_BACKEND_ADMIN_TOKEN`；产品签名密钥、授权码 pepper、实际设备码、权益、存储目录及数据集合仍隔离，不因接口统一而互认授权。
- 租约保留 `version=1` 和原文 Ed25519 签名，增加必需 `productId`；客户端必须先验原文，再检查产品、设备、权益与时钟。首次切换需要联网重新签发，不兼容旧缓存，也不修改设备前缀。
- 授权管理查询统一返回数组；`GET /admin/licenses?search=` 在数据库先筛选再取最多 200 条。本地 HTML 的 `Origin:null` 预检得到支持，Bearer 认证仍必需；到期时间在 UTC 与本地输入控件之间正确往返。
- 云端实际验签更新清单；两项目各自签名 JSON 与 BW 的 `data.program` 外壳保持现有格式。更新签名私钥不上传后端，登记默认停用、灰度 0%，同版本不可换文件。
- 新服务使用新的独立 MongoDB，配置待用户提供。尚未迁移旧授权记录、启用公网服务或发布客户端切换包；保留签名密钥不代表旧授权码自动存在于新库。
- BW 根启动器的新地址仍需人工 Core 交付；AI Program 的入口替换能力保持原有设计。完成数据库、密钥、对象存储及实际更新联调前，不能移除旧服务。
- BW 本轮修复了续租后指定权益已撤销却仍放行的问题；AI 已按指定权益检查，本轮补相同回归。新云端模块与完整接口以独立 `Mini_Backend/AI_README.md` 为准。
- 本轮证据：AI_Customer 完整后端 792 项通过、8 项既有跳过；BW 对方报告完整后端 195 项通过；新云端 Windows/Linux 各 11 项契约测试。正式授权密钥匹配双方公钥，服务器 Node.js 原文签名生成的隔离租约由两个真实 Python 客户端验签成功；未在线激活或写库。

## 1. 技术栈与架构

| 共性部分 | 方案 | 保留差异 |
| --- | --- | --- |
| 后端 | Python 3.11、FastAPI、Pydantic | 各自业务依赖及已验证的精确版本 |
| 前端 | Vue 3、Vite、Vue Router | AI_Customer 的 TypeScript / Element Plus；Blind_Watermark 的 JavaScript 与原组件 |
| 数据库 | 本地 SQLite；库启用 WAL，每连接启用外键、synchronous=NORMAL、30 秒等待 | 独立数据库、表与结构编号，不共库 |
| 浏览器 | CloakBrowser 与 Playwright；固定资源、账号 profile、任务资源互斥 | 不强制同步／异步模型相同；浏览器对象在所属线程创建、操作和关闭 |
| 主程序 | Nuitka 4.1.3 standalone，入口 onefile | 内部目录名称可不同；AI_Customer 语音组件保留已有 Nuitka 模块与 PyInstaller 运行依赖组织 |

路由承担请求校验与入队，服务处理业务，既有运行队列管理资源、取消、重试和中断恢复。外部写入任务不能因服务重启无条件重放。取消、浏览器关闭由执行路径协作完成，不能由别的线程随意调用 Playwright 对象。

采集不完整或子进程异常退出必须保持失败／中断含义，不得伪装成功。本轮核对 AI_Customer 的 CDP 浏览器在采集工作线程创建和关闭、异常退出不进入成功导入分支，没有为形式统一新增执行框架。

## 2. 交付与更新边界

| 产物 | 内容与用途 |
| --- | --- |
| Core | 单 ZIP 首装，包含入口、完整业务程序、必需浏览器和基础资源；AI_Customer 包含已编译 MyCrawler，Blind_Watermark 包含 U²-Net |
| Program | 日常签名更新，包含业务程序及全部直接运行依赖；不能包含账号、业务数据库、私钥、浏览器 profile 或必需模型 |
| 可选组件 | 只有真正可选的大组件独立交付，例如 AI_Customer 语音运行时与模型；不能把必需模型改成可选 |

客户解压 Core 后启动根目录入口，不再手工合并两个首装 ZIP。基础环境版本独立，环境不满足新程序要求时停止并要求新的 Core；本轮不开放自动远程交换基础环境。

入口保持稳定路径：AI_Customer 的 Program 已包含根入口，并由退出后的外部安装进程安全替换；Blind_Watermark 本轮的 Program 只替换 program/ 与清单，根入口版本提升通过人工交付 Core。后者将来若要自动升级根入口，需要单独验证外部安装进程和旧入口退出，不得宣称当前已支持。

AI_Customer 当前过程：检查 → 后台下载 → 验签、大小、哈希与路径校验 → 准备 → 用户确认 → 安全退出 → 安装 → 候选健康检查 → 提交或恢复。

Blind_Watermark 本轮仍在队列空闲后退出业务进程，由稳定启动器确认、下载和安装；本轮补事务恢复与健康检查，尚未实现业务运行时后台下载。将来对齐下载时机须另行实施，不能把共同推荐流程写成两边已经相同。

- AI_Customer 检查／下载不会关闭业务应用；双方安装前均拒绝未结束的有副作用操作。
- 备份完整、可验证后写入持久 prepared 事务，健康后写 committed；启动先恢复未提交事务。
- 恢复本身中断后应可重试；恢复不能依赖内存布尔量或只依赖 try/except。
- 拒绝 ZIP 路径穿越、重名／大小写冲突、链接及 junction 重定向；限制解压体积。
- 校验旧文件与备份，保护 data/ 和独立组件；失败材料按各自既有清理策略处理，不强求删除行为相同。
- 同版本不覆盖发布；未明确 ZIP／版本不发布；多个组件候选由用户明确选择，不按修改时间猜测。

刷入文件与持久日志能降低缓存和异常退出风险；源码模拟中断不能证明磁盘故障、所有硬断电情形或恶意恢复整机快照均安全。

## 3. 数据库

当前 AI_Customer 结构版本 16，Blind_Watermark 结构版本 11。PRAGMA user_version 是实际结构标识，不能用程序常量代替健康接口的数据库读取结果。

本阶段同结构更新；不同结构在替换前拒绝，不自动清库，不恢复全部旧迁移链。普通业务优化先在代码层处理，只有确需变更表结构时再设计该次迁移。将来真实客户跨结构更新时，另行确定前向迁移、SQLite Backup API 备份和失败恢复；程序目录回滚不能冒充数据库迁移回滚。

## 4. 版本、字段和单位

通用版本比较采用 SemVer 2.0.0：数字预发布段按数值比较，数字段优先级低于文本段，同核心版本正式版高于预发布版，构建标识不影响排序。比如 beta.10 高于 beta.2。

线上发行入口继续使用严格 X.Y.Z[-prerelease] 子集，拒绝前导零、空段和 +metadata。完整比较支持构建标识不等于当前线上发布支持构建标识。stable 不接收预发布版。

现有云端请求与签名原文保持各自字段，禁止一次全量改名。新本地配置／API 优先 snake_case；既有 Sealos 请求继续 camelCase。以下是语义映射，不要求已签名 JSON 改名：

| 含义 | AI_Customer | Blind_Watermark |
| --- | --- | --- |
| 安装程序版本 | release-manifest.json 的 version | release-manifest.json 的 program_version |
| 程序所需环境 | environment_version | required_environment_version |
| 数据库结构 | schema_version | schema_version |
| 安装更新器要求 | updater_version；签名要求 min_updater_version | minimum_launcher_version |
| 实际健康程序版本 | version | version / program_version |
| 实际健康环境 | environment_version | environment_version |
| 实际健康更新器 | updater_version | updater_version |
| 实际进程与结构 | process_id / schema_version | process_id / schema_version |
| 产品身份 | product：ai-customer | product：产品独立标识 |
| 构建溯源 | source_revision / source_dirty / built_at | git_commit / built_at；本轮按实际清单记录，不伪造缺失字段 |

健康接口共同返回 status、product、packaged、程序版本、实际环境、实际结构、实际更新器及 process_id。开发环境的 environment_version 为空。安装器核对候选真实进程和清单要求全部匹配，不能只接受 HTTP 200 或代码中的版本常量。

云端更新请求保留 licenseCode、deviceId、currentVersion、updaterVersion 等各自既有字段；Blind_Watermark 的 environmentVersion 请求也保留。授权响应的 leaseText、signature 及租约内 deviceId、issuedAt、expiresAt、entitlements 保持原格式，必须先验签原文；新通用后端租约增加必需 productId，不接受缺少产品身份的旧离线缓存。

| 参数 | 格式／单位 |
| --- | --- |
| 程序、环境、更新器版本 | 版本字符串；不能转浮点数 |
| schema_version、清单 format | 非负整数／协议编号，不参与 SemVer 排序 |
| size、package.size 等包大小 | 字节；整数 |
| SHA-256 | 64 位十六进制字符串；校验实际字节 |
| process_id | 整数；应匹配本次候选进程 |
| built_at、issuedAt、expiresAt | 带时区 ISO 8601 字符串；新产生时间统一 UTC |
| HTTP / 进程等待超时 | 秒；前端定时器是毫秒，命名时须明确单位 |
| rolloutPercent | 0～100 的整数百分比 |

已有业务库中的本地时间字段本轮不改存储格式，避免为命名和时区形式改表。产品常量、设备前缀、端口、桶、签名密钥与权益独立配置，不能从另一个产品复制身份。

## 5. 授权

双方使用 Windows DPAPI 机器级保护的随机设备秘密，产品命名空间独立；授权与更新分别验 Ed25519 签名，私钥不进入客户端。

正常离线租约 72 小时，6 小时后尝试续租；客户端限制异常超长租约。拒绝过期租约、设备或权益不匹配、无效签名；云端明确拒绝后不得继续借旧缓存放行。续租网络失败后再次确认租约仍未到期。

独立 license_clock.bin 与设备身份存放一起，不进入业务数据库备份。UTC 高水位、进程单调计时及 5 分钟容差检测回拨；正常最多每分钟落盘一次。检测后持续要求在线确认，改回系统时间或重放旧响应不能解锁；缺失／损坏的时间记录不能离线放行。新在线响应须先通过签名、设备、权益和时间校验再更新锚点。

DPAPI 临时文件先 flush/fsync 再原子替换，失败保留旧记录。长时间服务按各自运行策略检查授权；AI_Customer 私信接收共享守护线程，不为每个账号独立请求云端。离线期间无法即时获知远端撤销，本机管理员或修改客户端也不在“不可破解”承诺内。

## 6. 依赖与脚本

锁定各自当前直接依赖，提交前端 package-lock.json；开发测试、打包工具与业务运行依赖分层。AI_Customer 已精确锁定全部前端直接依赖，包括 Vue Router 4.6.4，后端和 Nuitka 保持已有精确版本。本轮不安装新库、不全量升级或降级。

统一具体基础版本须经两项目各自回归与发行验证后确定；当前版本不同不构成必须立即修复的问题。CloakBrowser 0.4.8 只是对方现有基线，尚未宣称通过 AI_Customer 发行验收。

AI_Customer 日常 script 入口继续仅 start_all.ps1、build_package.ps1、publish_release.ps1；共用格式校验与组装等辅助工具在 internal/。状态管理不依赖签名私钥；脚本不隐式安装、不自动发版，不修改调用者的工作目录或 PYTHONPATH。

开发服务端口独立：AI_Customer 后端 8000／前端 5173；Blind_Watermark 后端 8001／前端 5174。健康检查不得因同机另一服务占用端口而误判成功。

## 7. 验证要求与当前证据

双方独立测试相同契约向量：beta.2 与 beta.10、稳定版与预发布版、构建标识忽略、无效数字／空段、实际环境和 PRAGMA、错误 PID、安装中断与恢复中断、授权时钟及续租到期竞态、发行包不含业务数据。

本轮 AI_Customer 完整后端 781 项通过、8 项按既有条件跳过；12 个 PowerShell 脚本完成 Windows PowerShell 5.1 解析与 BOM 检查，全部前端直接依赖匹配精确锁文件。版本、脚本、身份／时钟定向回归包含在这些范围中，不重复累加测试量。

本规范描述源码行为与测试证据。真实 EXE 启动、Core 首装、Program 原地升级、失败恢复、Sealos 发布及平台操作应在明确的发行／验收任务中验证；本轮不执行这些操作。
