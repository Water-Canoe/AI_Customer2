# Sealos 后端接口

新服务根地址：`https://puxaxxvlzefd.sealosbja.site`。2026-10-12 已复用原有 MongoDB 实例的独立 `mini_backend` 库并启动 DevBox 服务，公网健康与管理认证通过；未发布客户端更新或执行镜像发布。

AI_Customer 产品前缀为 `/api/v1/products/ai-customer`；Blind_Watermark 使用 `/api/v1/products/blind-watermark`。新 DevBox 代码在 `/home/devbox/project/src/`，本地源代码在独立 `Mini_Backend` 工程。旧 Sealos 服务保持运行且未被修改，当前没有旧授权或发布记录迁移。完整云端配置和模块说明见 [通用后端文档](../../../Mini_Backend/AI_README.md)。

客户端与发行脚本仅用 `MINI_BACKEND_URL` 覆盖根地址，由产品配置派生路径。真实 MongoDB 授权/设备事务及轻量数据写冲突已验证；旧授权尚未导入，真实客户端切换和对象包收发未验收，旧地址不能提前移除。

后续换 MongoDB 实例：暂停本服务写入、完整备份/恢复 `mini_backend` 的数据和索引，保留记录 ID、绑定及现有 pepper/签名密钥，再改服务端 `MONGODB_URL` 与必要的 `DB_NAME`。公网后端地址不变时客户端无需因此更新。跨大版本、跨结构或更换数据库类型须单独设计；对象存储安装包不包含在 MongoDB 备份里。

## 授权模型

- 一个客户使用一个产品授权码，不再按工作台拆分授权码。
- 一个 Windows 安装使用一个机器设备码：客户端从 DPAPI 机器级身份派生，不信任可复制的业务数据库设备码。
- 授权权益支持 `lead`（拓客）、`traffic`（引流）、`content`（内容）。
- `ai-customer_licenses` 保存用于匹配的 HMAC-SHA-256 摘要、可检索前缀和 AES-256-GCM 完整授权码密文；只有管理员列表接口会解密返回完整码。
- 密文密钥使用 HKDF-SHA-256 从高熵 `AI_CUSTOMER_LICENSE_CODE_PEPPER` 派生并隔离用途，不新增可提交的秘密配置。新独立数据库不自动导入原授权记录；保留私钥并不代表旧授权码自动可用。
- `ai-customer_devices` 保存激活设备，`licenseId + deviceId` 唯一。
- 设备名额在 MongoDB 事务内原子占用和释放。
- 服务端签发 72 小时 Ed25519 租约；客户端本地验签，签发超过 6 小时才尝试续租。
- 客户端将授权时间记录独立保存在设备身份目录的 `license_clock.bin`，通过 DPAPI 保护；检测时钟回拨、记录缺失或损坏后要求在线确认。激活／续租响应应使用当前签发时间，与本机时间允许 5 分钟偏差；异常恢复不能重放上一次签名租约。时间保护继续复用激活/续租接口；新通用后端另增加签名产品身份，不修改本地数据库结构。
- 私信接收由客户端共用守护线程每 30 秒检查授权，有效缓存优先使用，仍沿用 6 小时续租阈值及正常 72 小时离线期限；失效后停止两平台接收和重连。本地检查不是每 30 秒请求一次云端的网络心跳。
- 不使用心跳、WebSocket 或同时在线检测，也不采集 CPU、主板等物理硬件序列号；设备边界由 Windows DPAPI 机器身份确定。
- 旧版设备不自动换绑；管理员通过现有设备撤销接口释放旧设备名额，再由新版客户端重新激活。

服务端 `.env` 必须配置以下秘密，实际值不得提交到 Git：

```text
MINI_BACKEND_ADMIN_TOKEN=<至少32字符>
AI_CUSTOMER_LICENSE_CODE_PEPPER=<随机高熵秘密>
AI_CUSTOMER_LICENSE_PRIVATE_KEY=<Ed25519 PKCS8 DER 的 Base64>
```

授权签名密钥与版本更新签名密钥必须分开。

## 公共接口

### 健康检查

`GET /api/v1/products/ai-customer/healthz`

实际 ping 数据库后只返回状态，不暴露数据库名、集合名或配置。根地址 `/healthz` 也可检查。对象存储通过实际上传/登记联调确认，不把数据库健康等同于存储可用。

### 首次激活

`POST /api/v1/products/ai-customer/license/activate`

```json
{
  "licenseCode": "AIC-...",
  "deviceId": "AI-CUS-XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX",
  "deviceName": "办公室电脑",
  "appVersion": "1.2.1"
}
```

设备不存在且名额充足时激活；同一设备再次调用时直接续租。成功响应核心字段：

```json
{
  "permission": true,
  "reason": "DEVICE_ACTIVATED",
  "boundNewDevice": true,
  "maxDevices": 3,
  "activeDeviceCount": 1,
  "entitlements": ["lead", "traffic", "content"],
  "leaseText": "{...原始JSON文本...}",
  "signature": "Base64 Ed25519签名"
}
```

`leaseText` 必须按原始 UTF-8 字节验签，不得解析后重新序列化再验签；`productId` 必须为 `ai-customer`，首次切换需联网重新签发，旧离线缓存不兼容。租约内容为：

```json
{
  "version": 1,
  "productId": "ai-customer",
  "licenseId": "...",
  "deviceId": "AI-CUS-XXXXXXXX-XXXXXXXX",
  "entitlements": ["content", "lead", "traffic"],
  "issuedAt": "2026-07-15T00:00:00.000Z",
  "expiresAt": "2026-07-18T00:00:00.000Z"
}
```

### 续租

`POST /api/v1/products/ai-customer/license/renew`

请求体与首次激活相同，但只允许已激活且仍为 `active` 的设备，不会占用新设备名额。

常见拒绝原因：

- `LICENSE_NOT_FOUND`：授权码不存在。
- `LICENSE_DISABLED`：授权已停用。
- `LICENSE_EXPIRED`：授权已过期。
- `DEVICE_NOT_BOUND`：续租设备从未激活。
- `DEVICE_REVOKED`：设备已被管理员撤销。
- `DEVICE_LIMIT_EXCEEDED`：激活设备数已满。
- `RATE_LIMITED`：短时间尝试过多。

## 管理接口

所有管理请求必须携带：

```text
Authorization: Bearer <MINI_BACKEND_ADMIN_TOKEN>
```

Token 不允许放在 URL，不应写入浏览器 `localStorage` 或日志。

### 创建授权

`POST /api/v1/products/ai-customer/admin/licenses`

```json
{
  "entitlements": ["lead", "traffic", "content"],
  "maxDevices": 3,
  "expiresAt": "2027-01-01T00:00:00.000Z",
  "remark": "客户备注"
}
```

`expiresAt` 可为 `null`，表示长期。响应中的 `licenseCode` 会立即返回，并以 AES-256-GCM 密文写入数据库供管理员列表再次查看。

### 列表与修改

- `GET /api/v1/products/ai-customer/admin/licenses?search=`：数组响应，数据库先筛选再取最近 200 条；完整授权码通过 HMAC 精确匹配、ID 精确匹配、前缀与备注按字面检索。管理员收到解密后的完整 `licenseCode`。
- `PATCH /api/v1/products/ai-customer/admin/licenses/{licenseId}`：修改 `status / entitlements / maxDevices / expiresAt / remark`。

`status` 只支持 `active` 或 `disabled`。`maxDevices` 不能低于当前已激活设备数，否则返回 `409 / MAX_DEVICES_BELOW_ACTIVE`。授权不提供硬删除；商业记录需要保留时使用停用。

### 设备管理

- `GET /api/v1/products/ai-customer/admin/licenses/{licenseId}/devices`：查看设备。
- `POST /api/v1/products/ai-customer/admin/licenses/{licenseId}/devices/{deviceId}/revoke`：撤销设备并释放一个名额。
- `POST /api/v1/products/ai-customer/admin/licenses/{licenseId}/devices/{deviceId}/restore`：恢复已撤销设备并重新占用一个名额。

恢复只接受 `revoked` 设备；授权已停用、已过期或名额已满时拒绝恢复。设备已经是 `active` 时重复调用不会重复增加设备计数。成功响应会返回 `restoredAt`，设备列表也会返回最近一次恢复时间。

根目录 `tools/license-admin.html` 已接入以上接口，可直接在浏览器打开；搜索按钮/回车请求数据库，状态与权益筛选针对返回的最多 200 条。新服务支持此本地页面的 `Origin:null` 预检，认证仍使用 Bearer。新根地址 `/admin` 还提供两产品共用管理页。页面包含授权统计、完整授权码直接展示与复制、搜索筛选、CSV 导出、授权编辑、设备撤销与恢复；管理 Token 和授权码都不会写入浏览器持久化存储，完整授权码只由管理员接口从服务端密文解密后返回。

## 远程更新

对象存储桶保持私有，Access Key 和 Secret Key 只配置在 Sealos `.env`。客户端和本地 Git 仓库不得保存对象存储凭证。

### 检查更新

`POST /api/v1/products/ai-customer/update/check`

```json
{
  "licenseCode": "AIC-...",
  "deviceId": "AI-CUS-XXXXXXXX-XXXXXXXX",
  "currentVersion": "1.2.0",
  "updaterVersion": "1.0.0",
  "channel": "stable",
  "platform": "windows",
  "arch": "x64"
}
```

更新检查不会激活新设备，授权和设备必须仍为 `active`。有更新时返回原始 `manifestText`、Ed25519 `signature` 和限时 `downloadUrl`；客户端先验签，再校验 ZIP 大小和 SHA-256。

### 发布管理

- `POST /api/v1/products/ai-customer/update/admin/upload-url`：生成不可覆盖的限时上传地址。
- `POST /api/v1/products/ai-customer/update/admin/releases`：实际校验原文签名并登记版本元数据，默认停用、灰度 0%。
- `GET /api/v1/products/ai-customer/update/admin/releases`：查询最多 200 个版本，包含 `releaseId`。
- `PUT /api/v1/products/ai-customer/update/admin/release-status`：调整启用、强制更新和灰度状态。

这些接口与授权管理共用 `Authorization: Bearer <MINI_BACKEND_ADMIN_TOKEN>`。

正式上传、查询与状态操作需在当前 PowerShell 会话中显式设置 `MINI_BACKEND_ADMIN_TOKEN`，不再通过 SSH 从旧部署读取。`ServerBaseUrl`、`SshKeyPath` 参数已移除；地址从客户端产品配置读取。管理令牌实际值不要写入脚本或提交 Git。

日常发布使用：

```powershell
# 明确指定已完成的新版本，仅本地验证和签名，不访问 Sealos。
.\script\publish_release.ps1 -Version 1.3.0

# 上传并登记该版本，暂不向客户下发。
.\script\publish_release.ps1 -Version 1.3.0 -Upload

# 上传、登记并启用该版本；显式选择 10% 灰度，默认值为 100%。
.\script\publish_release.ps1 -Version 1.3.0 -Enable -RolloutPercent 10
```

发布顺序：

1. 验证本地 Program ZIP，再获取管理令牌和限时 PUT 地址。
2. 发布机生成精简清单并使用独立 Ed25519 私钥签名、验签。
3. 向私有对象存储上传同一已验证 ZIP；网络失败时保留签名产物。
4. 登记 `manifestText` 和 Base64 签名，新记录默认禁用。
5. 修改 `enabled` 与 `rolloutPercent` 后开始下发。

每次申请上传使用独立随机对象，已登记的同一版本不能换文件；修正安装包必须递增版本号。`stable` 不接受预发布版本，`beta` 可使用 `1.2.0-beta.1`。服务端使用 `ai-customer_releases` 保存版本元数据，停用版本使用状态接口，不提供远程删除。

### 可选运行组件

音色克隆运行库不进入主程序包。已激活且授权包含 `content` 权益的客户端可检查组件：

`POST /api/v1/products/ai-customer/update/component/check`

```json
{
  "licenseCode": "AIC-...",
  "deviceId": "AI-CUS-XXXXXXXX-XXXXXXXX",
  "component": "voxcpm2",
  "currentVersion": "0.0.0",
  "appVersion": "1.2.1",
  "platform": "windows",
  "arch": "x64"
}
```

服务端只支持 `voxcpm2`，返回签名清单、限时下载地址和最低主程序版本。组件不会随主程序自动安装，必须由用户在内容设置页点击安装。

组件管理接口：

- `POST /api/v1/products/ai-customer/update/admin/component-upload-url`：生成独立随机对象的组件上传地址。
- `POST /api/v1/products/ai-customer/update/admin/components`：登记组件签名元数据，默认禁用。
- `GET /api/v1/products/ai-customer/update/admin/components`：查询组件版本。
- `PUT /api/v1/products/ai-customer/update/admin/component-status`：启用或停用组件版本。

日常组件发布使用：

```powershell
# 生成独立VoxCPM2/PyTorch运行组件，不生成模型权重。
.\script\internal\build_voxcpm_component.ps1 -Version 1.0.0

# 只在本地压缩、签名和验签。
.\script\internal\publish_voxcpm_component.ps1

# 上传并登记，但暂不允许客户安装。
.\script\internal\publish_voxcpm_component.ps1 -Upload

# 上传、登记并允许客户安装。
.\script\internal\publish_voxcpm_component.ps1 -Enable
```

组件对象路径前缀 `releases/ai-customer/voxcpm2/`，程序对象路径前缀 `releases/ai-customer/program/`；真实上传使用后端签发路径。组件使用独立版本号和 `ai-customer_components` 集合。同一组件版本不可覆盖；组件包只包含推理程序和依赖，模型权重仍由首次合成下载到稳定数据目录。

## 轻量数据接口

通用后端另外提供 `/data/:namespace/:key` 的 GET/PUT/DELETE，以及命名空间列表。认证使用客户授权码 Bearer 和 `X-Device-Id`；按产品与授权隔离，同授权设备共享。写入/删除需 `expectedVersion`，冲突返回 409，单条 JSON 最大 32 KiB；列表最多 100 项，不是累计容量配额。当前 AI_Customer 业务尚未使用这些 API。

## 历史接口边界

不保留旧设备兼容层，以下接口已删除：

- `/add-license`
- `/check-license`
- `/get-license-devices`
- `/revoke-license-device`
- `/get-permission`
- `/add-permission`
- `/get-permission-list`
