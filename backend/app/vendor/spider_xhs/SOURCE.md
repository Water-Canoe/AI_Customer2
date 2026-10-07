# Spider_XHS 私信依赖

- 来源：[cv-cat/Spider_XHS](https://github.com/cv-cat/Spider_XHS)
- 固定提交：`ebb6c4fbeaedf1237190ebc0c8e3ae8b7ddd030e`。
- 本目录只保留 PC Cookie 认证、签名状态、Cookie 域隔离、HTTP 连接及 IM 消息帧所需代码。`im.py` 从上游 `apis/xhs_live.py` 提取 Protobuf 编解码、RWP 帧构造、签名请求和令牌刷新，未带入直播、采集、发布、扫码或短信登录功能。
- 命名空间调整为 `app.vendor.spider_xhs`；Cookie 工厂通过提取的签名请求查询 `user/me`，不依赖整个 `XHS_Apis`。移除未使用的扫码/短信工厂及 profile 遥测接口；Node 运行时固定使用项目已有的 Playwright driver Node，并隐藏 Windows 子进程窗口。锚点刷新失败直接抛出，不使用过期锚点继续请求。
- 实际收发与连接监督位于 `app/services/xhs_im_transport.py`：WebSocket 接收使用上游 RWP 握手/注册/应用心跳；发送固定使用上游 `/api/im/web/short_link/send_message` 签名 HTTP 入口，不在失败时切换另一种发送方式。只在明确 `success=true, code=0` 时记录“已提交”，结果未知不自动重发。
- 共用业务不在 vendor 内：两个平台使用 `im_session.py` 加密认证存储、`im_transport.py` 认证缓存与用户 ID 校验，以及 `message_inbox.py` 会话/消息/客户状态逻辑。
- 上游未提供明确复用许可证，README 限定学习交流并禁止商业化。这里保留 `UPSTREAM_README.md` 和原始来源说明，用于当前本地研究；本次接入不构成上游商业使用或对外分发授权。
