AI拓客工具测试包使用说明

1. 双击 AI_Customer.exe 启动本地工作台。
2. 程序会自动打开浏览器页面；如果没有自动打开，请查看控制台里显示的 http://127.0.0.1:端口 地址。
3. 第一次使用请进入“设置”页，点击“授权与设备”，填写授权码并校验。
4. MediaCrawler 没有打进 exe。如果需要真实采集，请在“设置”页把 MediaCrawler 路径指向本机的 MediaCrawler 目录，并确认底层 SQLite 路径正确。
5. runtime/ai_customer.sqlite3 是本机业务数据库，会在程序目录旁自动创建。
6. 关闭 AI_Customer.exe 的控制台窗口即可停止本地服务。

说明：
- 测试包不包含你的 Python/Vue 源码。
- 本地包仍会调用 Sealos 授权接口校验授权码和设备数。
- 不要把自己的 AI API Key 打进包里，使用者需要在设置页自行填写。
