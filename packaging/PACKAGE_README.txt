AI拓客工具（便携版）

首次使用只有三步：

1. 解压 AI_Customer_Program_<版本>.zip。
2. 把 AI_Customer_Environment_<版本>.zip 解压到同一个目录；看到 runtime 文件夹时选择合并。
3. 双击最外层 AI_Customer.exe。

以后打开和关闭：

- 打开：只双击最外层 AI_Customer.exe，不要进入 runtime/application 手工运行 AI_Customer_App.exe。
- 关闭：关闭 AI_Customer_App 的运行窗口；稳定启动器会随之退出，不会保留旧服务进程。

目录说明：

- data：业务数据库、登录状态、内容资产、视频成品和备份，更新不会覆盖。
- runtime：统一运行环境，来自环境 ZIP；不需要安装 Python、Playwright、CloakBrowser、MyCrawler 或 VoxCPM2。
- updates：自动更新下载的临时文件。

启动时会自动检查新版本，但不会静默下载。发现更新后先显示版本、大小和更新说明；用户确认后显示真实下载与安装进度，完成后自动启动。普通更新可以稍后处理，强制更新需要先完成更新。更新只替换程序 ZIP 所属文件，不覆盖 data，也不覆盖环境依赖。若新程序明确要求新版环境，启动时会直接提示所需环境版本。

首次打开后，请进入“设置” > “授权与设备”，填写产品授权码并校验。
