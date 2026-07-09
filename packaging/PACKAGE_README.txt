AI拓客工具发布包使用说明

首次安装或更新：

# 校验发布清单，并安装到当前 Windows 用户的稳定目录
powershell -ExecutionPolicy Bypass -File ".\install-release.ps1" -ReleasePath "."

安装完成后：

1. 双击 %LOCALAPPDATA%\AI_Customer\AI_Customer.exe 启动工作台。
2. 第一次使用请进入“设置”，点击“授权与设备”，填写产品授权码并校验。
3. 采集组件路径由程序从稳定安装目录自动解析，客户端页面和接口不会显示本机路径。
4. data\ai_customer.sqlite3、备份、引流图片和登录状态都保存在稳定安装目录的 data 下。
5. 新版本安装到 versions\<版本号>，不会覆盖 data，也不会删除旧版本。
6. 发布包安装前会校验每个文件的大小和 SHA-256；校验失败时不会切换当前版本。
7. 采集、AI、自动私信和引流统一进入运行队列；日志页可以取消、重试或删除已结束的队列记录。
8. 恢复数据备份前请停止运行任务；程序检测到活动任务时会拒绝恢复。
9. 正常关闭程序会停止任务派发并回收登录窗口；脚本强制关闭后的任务会在下次启动时恢复为明确状态。

切回已安装旧版本：

# 只切换程序版本指针，不修改业务数据
powershell -ExecutionPolicy Bypass -File ".\switch-version.ps1" -Version "1.1.0"

安全说明：

- 发布包不包含本项目的 Python/Vue 源文件，Python 字节码使用优化级别 2 打包。
- 授权服务地址、采集路径和 API Key 不通过公共设置接口返回。
- 本地客户端无法做到绝对防逆向；正式授权和设备数量仍由远端服务校验。
- 不要把自己的 AI API Key 或包含客户数据的 data 目录放进发布包。
