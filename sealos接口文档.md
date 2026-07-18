# Sealos 后端接口

公网调试地址：`https://tfwqsfaegbdj.sealosbja.site`

AI拓客接口统一使用 `/ai-customer` 前缀，服务端代码位于 Sealos `~/project/routers/AI_Customer/`。原中医项目继续使用 `/medician`，本项目不得修改其业务接口。

## 授权模型

- 一个客户使用一个产品授权码，不再按工作台拆分授权码。
- 一个 Windows 安装使用一个机器设备码：客户端从 DPAPI 机器级身份派生，不信任可复制的业务数据库设备码。
- 授权权益支持 `lead`（拓客）、`traffic`（引流）、`content`（内容）。
- `AI_Customer-License` 只保存授权码 HMAC-SHA-256 摘要与前缀，完整授权码只在创建时返回一次。
- `AI_Customer-LicenseDevice` 保存激活设备，`licenseId + deviceId` 唯一。
- 设备名额在 MongoDB 事务内原子占用和释放。
- 服务端签发 72 小时 Ed25519 租约；客户端本地验签，签发超过 6 小时才尝试续租。
- 不使用心跳、WebSocket 或同时在线检测，也不采集 CPU、主板等物理硬件序列号；设备边界由 Windows DPAPI 机器身份确定。
- 旧版设备不自动换绑；管理员通过现有设备撤销接口释放旧设备名额，再由新版客户端重新激活。

服务端 `.env` 必须配置以下秘密，实际值不得提交到 Git：

```text
AI_CUSTOMER_ADMIN_TOKEN=<至少32字符>
AI_CUSTOMER_LICENSE_CODE_PEPPER=<随机高熵秘密>
AI_CUSTOMER_LICENSE_PRIVATE_KEY=<Ed25519 PKCS8 DER 的 Base64>
```

授权签名密钥与版本更新签名密钥必须分开。

## 公共接口

### 健康检查

`GET /ai-customer/healthz`

只返回服务是否正常，不暴露数据库名、集合名或配置。

### 首次激活

`POST /ai-customer/license/activate`

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

`leaseText` 必须按原始 UTF-8 字节验签，不得解析后重新序列化再验签。租约内容为：

```json
{
  "version": 1,
  "licenseId": "...",
  "deviceId": "AI-CUS-XXXXXXXX-XXXXXXXX",
  "entitlements": ["content", "lead", "traffic"],
  "issuedAt": "2026-07-15T00:00:00.000Z",
  "expiresAt": "2026-07-18T00:00:00.000Z"
}
```

### 续租

`POST /ai-customer/license/renew`

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
Authorization: Bearer <AI_CUSTOMER_ADMIN_TOKEN>
```

Token 不允许放在 URL，不应写入浏览器 `localStorage` 或日志。

### 创建授权

`POST /ai-customer/admin/licenses`

```json
{
  "entitlements": ["lead", "traffic", "content"],
  "maxDevices": 3,
  "expiresAt": "2027-01-01T00:00:00.000Z",
  "remark": "客户备注"
}
```

`expiresAt` 可为 `null`，表示长期。响应中的 `licenseCode` 只展示一次，之后列表只返回 `codePrefix`。

### 列表与修改

- `GET /ai-customer/admin/licenses`：最近 200 个授权。
- `PATCH /ai-customer/admin/licenses/{licenseId}`：修改 `status / entitlements / maxDevices / expiresAt / remark`。

`status` 只支持 `active` 或 `disabled`。授权不提供硬删除；商业记录需要保留时使用停用。

### 设备管理

- `GET /ai-customer/admin/licenses/{licenseId}/devices`：查看设备。
- `POST /ai-customer/admin/licenses/{licenseId}/devices/{deviceId}/revoke`：撤销设备并释放一个名额。

根目录 `tools/license-admin.html` 已接入以上接口，可直接在浏览器打开。完整授权码和管理 Token 都不会持久化到页面存储。

## 远程更新

对象存储桶保持私有，Access Key 和 Secret Key 只配置在 Sealos `.env`。客户端和本地 Git 仓库不得保存对象存储凭证。

### 检查更新

`POST /ai-customer/update/check`

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

- `GET /ai-customer/update/health`：检查更新数据库和对象存储。
- `POST /ai-customer/update/admin/upload-url`：生成不可覆盖的限时上传地址。
- `POST /ai-customer/update/admin/releases`：登记签名版本元数据。
- `GET /ai-customer/update/admin/releases`：查询版本列表。
- `PUT /ai-customer/update/admin/release-status`：调整启用、强制更新和灰度状态。

这些接口与授权管理共用 `Authorization: Bearer <AI_CUSTOMER_ADMIN_TOKEN>`。

日常发布使用：

```powershell
# 只在本地校验、压缩、签名和验签，不访问 Sealos。
.\script\publish_release.ps1

# 上传并登记最新版本，但暂不向客户下发。
.\script\publish_release.ps1 -Upload

# 上传、登记，并按默认10%灰度启用。
.\script\publish_release.ps1 -Enable
```

发布顺序：

1. 获取限时 PUT 地址。
2. 向私有对象存储上传 ZIP。
3. 发布机生成精简清单并使用独立 Ed25519 私钥签名。
4. 登记 `manifestText` 和 Base64 签名，新记录默认禁用。
5. 修改 `enabled` 与 `rolloutPercent` 后开始下发。

同一版本对象和版本元数据都不可覆盖；修正安装包必须递增版本号。`stable` 不接受预发布版本，`beta` 可使用 `1.2.0-beta.1`。服务端使用 `AI_Customer-Release` 保存版本元数据，停用版本使用状态接口，不提供远程删除。

### 可选运行组件

音色克隆运行库不进入主程序包。已激活且授权包含 `content` 权益的客户端可检查组件：

`POST /ai-customer/update/component/check`

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

- `POST /ai-customer/update/admin/component-upload-url`：生成不可覆盖的组件上传地址。
- `POST /ai-customer/update/admin/components`：登记组件签名元数据，默认禁用。
- `GET /ai-customer/update/admin/components`：查询组件版本。
- `PUT /ai-customer/update/admin/component-status`：启用或停用组件版本。

日常组件发布使用：

```powershell
# 生成独立VoxCPM2/PyTorch运行组件，不生成模型权重。
.\script\build_voxcpm_component.ps1 -Version 1.0.0

# 只在本地压缩、签名和验签。
.\script\publish_voxcpm_component.ps1

# 上传并登记，但暂不允许客户安装。
.\script\publish_voxcpm_component.ps1 -Upload

# 上传、登记并允许客户安装。
.\script\publish_voxcpm_component.ps1 -Enable
```

组件使用独立版本号和 `AI_Customer-ComponentRelease` 集合。同一组件版本不可覆盖；组件包只包含推理程序和依赖，模型权重仍由首次合成下载到稳定数据目录。

## 已删除接口

不保留旧设备兼容层，以下接口已删除：

- `/add-license`
- `/check-license`
- `/get-license-devices`
- `/revoke-license-device`
- `/get-permission`
- `/add-permission`
- `/get-permission-list`
