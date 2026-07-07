# 抖音私信自动化测试

这是一个独立的单用户测试工具，用 CloakBrowser 的 Playwright 兼容接口打开最大化浏览器窗口，访问抖音用户主页，点击私信入口，输入话术，并按需点击发送。

## 文件

- `automation.py`：可直接运行的自动化脚本。
- `server.py`：FastAPI 服务，给 `index.html` 提供调用接口。
- `index.html`：最小前端页面。
- `../../data/douyin_cloak_profile/`：和拓客、引流工作台共用的 CloakBrowser 登录态目录，不提交。

## 命令

```powershell
# 进入项目根目录，保证后续相对路径正确
cd D:\Dev\Projects\Web_Project\AI_Customer

# 运行脚本自检，只检查参数校验，不打开浏览器
backend\.venv\Scripts\python.exe tools\douyin_dm_automation\automation.py --self-check

# 启动前端页面服务
backend\.venv\Scripts\python.exe -m uvicorn tools.douyin_dm_automation.server:app --host 127.0.0.1 --port 8025

# 直接用命令行测试；--dry-run 表示只输入话术，不点击发送
backend\.venv\Scripts\python.exe tools\douyin_dm_automation\automation.py --user-url "https://www.douyin.com/user/MS4wLjABAAAAY0Ci-RnZrri7dWXZI5BxIBoS-Q42xcYsAsRHMfjaSFtGxRPr0hiisLjww-_DRqJI" --message "你好，我这边想测试一下抖音网页版私信自动化。" --dry-run
```

打开 `http://127.0.0.1:8025/` 后填写用户主页 URL 和话术。首次运行会弹出浏览器；如果抖音提示未登录，就在该浏览器里完成登录，脚本最长等待 300 秒。抖音页面会渲染一个隐藏的“私信”按钮副本，脚本会点击最后一个可见按钮；点击后 25 秒内没有出现聊天输入框就报错，不再长时间卡住。

如果修改了脚本代码，需要重启 `uvicorn` 服务；旧服务进程不会自动加载新版点击逻辑。

## 限制

当前版本只做单用户测试，不做批量发送、账号池、代理池或自动重试队列。抖音的发送按钮是输入区右侧红色圆形上箭头，脚本会点击最后一个可见发送 SVG 的中心点，并校验聊天记录里是否出现本人发送的消息气泡。抖音页面结构如果变化，优先调整 `PROFILE_DM_SELECTORS`、`CHAT_INPUT_SELECTORS`、`SEND_SELECTORS` 和 `SEND_ICON_SELECTORS`。
