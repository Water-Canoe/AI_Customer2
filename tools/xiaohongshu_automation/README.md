# 小红书 Web 自动化验证

这个目录先提供小红书 Web 登录窗口。后续动作验证复用同一个 `runtime/cloak_profile/` 登录态。

## 命令

```powershell
# 进入项目根目录，保证相对路径正确
cd D:\Dev\Projects\Web_Project\AI_Customer

# 打开小红书登录窗口；首次运行先在窗口里完成登录
backend\.venv\Scripts\python.exe tools\xiaohongshu_automation\open_login_browser.py
```
