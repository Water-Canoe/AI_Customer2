# sealos后端配置
公网调试地址：https://tfwqsfaegbdj.sealosbja.site
公网运行地址：https://pcshudjkxyqb.sealosbja.site

# 路由规划

- AI拓客项目接口统一挂载在 `/ai-customer` 前缀下，代码目录为 `~/project/routers/AI_Customer/`。
- 原中医项目接口继续挂载在 `/medician` 前缀下，代码目录调整为 `~/project/routers/AI_Medician/`。
- 根路径 `/get-permission` 不再提供 AI拓客权限接口；请使用 `/ai-customer/get-permission`。

# 当前接口

- `GET /ai-customer/db-health`：检查 MongoDB 连接。
- `POST /ai-customer/add-license`：添加授权码，默认最多绑定 3 台设备。
- `POST /ai-customer/check-license`：校验授权码并自动绑定设备；如果设备不存在且当前 active 设备数小于 `maxDevices`，自动绑定；如果 active 设备数已满，拒绝授权。
- `POST /ai-customer/get-license-devices`：查询授权码下的设备列表。
- `POST /ai-customer/revoke-license-device`：解绑某个授权设备，状态改为 `revoked`，释放 active 设备名额。
- `POST /ai-customer/get-permission`：旧 demo，按 `deviceId` 查询是否授权。
- `POST /ai-customer/add-permission`：旧 demo，添加测试授权设备。
- `POST /ai-customer/get-permission-list`：旧 demo，查询某个 `deviceId` 的授权记录列表。

PowerShell 测试 JSON 请求时，推荐把请求体写入临时 JSON 文件，再用 `curl.exe --data-binary "@文件路径"` 发送，避免 Windows 命令行把 JSON 双引号吃掉。

# 授权码与设备限制

使用两个 MongoDB 集合：

- `AI_Customer-License`：授权码主表。
- `AI_Customer-LicenseDevice`：授权码绑定设备表。

当前不做同时在线状态，只做“最多绑定设备数”限制。

## 添加授权码

`POST /ai-customer/add-license`

```json
{
  "licenseCode": "LIC-XXXX",
  "maxDevices": 3,
  "expireTime": "2027-01-01T00:00:00.000Z",
  "remark": "客户备注"
}
```

`maxDevices` 为空时默认 `3`。`expireTime` 可为空，表示暂不过期。

## 校验授权并自动绑定设备

`POST /ai-customer/check-license`

```json
{
  "licenseCode": "LIC-XXXX",
  "deviceId": "device-fingerprint",
  "deviceName": "办公室电脑",
  "remark": "备注"
}
```

判断逻辑：

1. 授权码不存在：返回 `permission=false`，`reason=LICENSE_NOT_FOUND`。
2. 授权码不是 `active`：返回 `permission=false`，`reason=LICENSE_DISABLED`。
3. 授权码过期：返回 `permission=false`，`reason=LICENSE_EXPIRED`。
4. 设备已绑定且 `active`：返回 `permission=true`，`reason=DEVICE_ALREADY_BOUND`，并更新 `lastSeenTime`。
5. 设备已解绑：返回 `permission=false`，`reason=DEVICE_REVOKED`。
6. 设备未绑定且 active 设备数 `>= maxDevices`：返回 `permission=false`，`reason=DEVICE_LIMIT_EXCEEDED`。
7. 设备未绑定且 active 设备数 `< maxDevices`：自动绑定设备，返回 `permission=true`，`reason=DEVICE_BOUND`。

## 查询授权设备

`POST /ai-customer/get-license-devices`

```json
{
  "licenseCode": "LIC-XXXX"
}
```

返回授权码信息和设备列表，`activeDeviceCount` 只统计 `status=active` 的设备。

## 解绑设备

`POST /ai-customer/revoke-license-device`

```json
{
  "licenseCode": "LIC-XXXX",
  "deviceId": "device-fingerprint"
}
```

解绑只把设备状态改为 `revoked`，不删除历史记录。解绑后 active 设备数减少，新的设备可以继续绑定。

# 旧 demo
```js
const express = require('express'); // 导入express
const router = express.Router();    // 创建路由实例
const { getDB } = require('../../config/database');

router.get('/db-health', async (req, res) => {
  try {
    const db = getDB();
    if (!db) {
      return res.status(500).json({
        code: 500,
        message: 'db为空，数据库未初始化',
        data: null
      });
    }

    await db.command({ ping: 1 });

    return res.status(200).json({
      code: 200,
      message: '数据库连接正常',
      data: {
        databaseName: db.databaseName
      }
    });
  } catch (error) {
    return res.status(500).json({
      code: 500,
      message: error.message,
      data: null
    });
  }
});

router.post('/get-permission', async(req, res) => { //获取权限
    const db = getDB();
    const { deviceId } = req.body || {};
    const devicePermission = await db.collection('AI_Customer-Permission').findOne({deviceId});
    if(devicePermission){
        const permissionId = devicePermission._id.toString();
        return res.status(200).json({
            code: 200,
            message: '获取权限成功',
            data: {
                permission: true,
                permissionId: permissionId
            }
        })
    }
    return res.status(200).json({
        code: 200,
        message: '获取权限失败',
        data: {
            permission: false,
            permissionId: null
        }
    })
});

router.post('/add-permission', async(req, res) => { //添加权限
    const db = getDB();
    const { deviceId, remark } = req.body || {};
    const permissionData = {
        deviceId: deviceId,
        remark: remark || '',  //备注
        createTime: new Date()
    };
    const result = await db.collection('AI_Customer-Permission').insertOne(permissionData);
    if(result.insertedId){
        return res.status(200).json({
            code: 200,
            message: '添加权限成功',
            data: {
                permissionId: result.insertedId.toString()
            }
        })
    }
    return res.status(500).json({
        code: 500,
        message: '添加权限失败',
        data: null
    });
});

router.post('/get-permission-list', async(req, res) => { //获取权限列表
    const db = getDB();
    const { deviceId } = req.body || {};
    const permissionList = await db.collection('AI_Customer-Permission').find({deviceId: deviceId}).toArray();
    return res.status(200).json({
        code: 200,
        message: '获取权限列表成功',
        data: permissionList
    })
});


module.exports = router;
```
