# 快手 Web 自动化验证

这个目录用于验证快手 Web 端能否被 CloakBrowser + Playwright 自动化。当前实测成功的是推荐流里的关注、点赞、收藏、文字评论；当前 Web 没有发现私信入口，也没有评论图片上传控件。

## 文件

- `open_login_browser.py`：打开最大化 CloakBrowser 快手窗口，供手动登录。
- `automation.py`：最小 CLI，复用同一个 `runtime/cloak_profile/` 登录态执行或探测动作。
- `runtime/`：本机登录态和日志目录，已被 git 忽略。

## 命令

```powershell
# 进入项目根目录，保证相对路径正确
cd D:\Dev\Projects\Web_Project\AI_Customer

# 打开快手登录窗口；首次运行先在窗口里完成登录
backend\.venv\Scripts\python.exe tools\kuaishou_dm_automation\open_login_browser.py

# 只做参数自检，不打开浏览器
backend\.venv\Scripts\python.exe tools\kuaishou_dm_automation\automation.py --self-check

# 在推荐流当前视频执行关注、点赞、收藏和文字评论
backend\.venv\Scripts\python.exe tools\kuaishou_dm_automation\automation.py --actions follow,like,favorite --comment "codex-auto-test-ignore"

# 检查指定主页是否暴露私信入口
backend\.venv\Scripts\python.exe tools\kuaishou_dm_automation\automation.py --profile-url "https://www.kuaishou.com/profile/3xqhxwgsjvvwpe9"
```

## 已验证结果

- 关注作者：右侧头像下方红色加号，接口 `POST /rest/v/relation/follow` 返回 `result=1`。
- 点赞视频：右侧心形按钮，接口 `POST /rest/v/photo/like` 返回 `result=1`。
- 收藏视频：右侧星形按钮，接口 `POST /rest/v/photo/collect` 返回 `result=1`。
- 文字评论：打开右侧评论面板，填 `.comment-input input`，点击 `.send-btn`，接口 `POST /rest/v/photo/comment/add` 返回 `result=1`。
- 评论图片：评论面板只有文字输入、表情按钮和发送按钮，未暴露 `input[type=file]`，当前 Web 端不支持。
- 私信：`www.kuaishou.com/profile/...` 只有关注和举报；`live.kuaishou.com/profile/...` 只有关注和房间；常见 `/message`、`/messages`、`/im`、`/chat` 路由返回 404。当前 Web 端不支持自动私信。

## 注意

快手页面坐标受窗口大小和设备缩放影响。脚本先用 DOM 类名找元素中心，再用 `page.mouse.click()` 点击，避免普通 locator click 被快手页面滚动和遮挡逻辑卡住。
