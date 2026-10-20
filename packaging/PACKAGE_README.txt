AI拓客工具（便携版）

首次使用：

1. 将 AI_Customer.zip 解压到固定的 AI_Customer 目录，目录名不带版本号。
2. 双击该目录中的 AI_Customer.exe，无需安装 Python 或另外合并环境包。

以后更新始终在同一安装目录进行，不创建另一个版本目录，也不自动改名。实际版本以软件内显示和 release-manifest.json 为准；开发交付仍按 deliverables/<版本>/ 分开保存。

语音克隆为可选组件：需要时取得 AI_Customer_Voice_<版本>.zip，解压到同一安装目录并合并 runtime，再重启软件。采集、筛选、客户管理、私信不依赖语音组件。

以后打开和关闭：

- 打开：只双击最外层 AI_Customer.exe，不要进入 runtime/application 手工运行 AI_Customer_App.exe。
- 关闭：点击页面左下角“安全退出”，正常停止任务队列、浏览器会话和本地服务。
- 日志：后端不显示命令行窗口，运行记录保存在 data/logs/app.log。

目录说明：

- data：业务数据库、登录状态、内容资产、视频成品和备份，更新不会覆盖。
- runtime/application 和 runtime/frontend_dist：完整程序及其运行依赖，随程序更新。
- runtime/MyCrawler 和 runtime/cloakbrowser_browser：Core 自带的基础环境，由日常更新保留。
- runtime/components 和 runtime/models：按需安装的语音运行时与模型。
- updates：自动更新下载的临时文件。

日常更新使用 AI_Customer_Program_<版本>.zip，首次安装请使用 AI_Customer.zip。更新只替换程序清单所属文件（包括程序依赖和入口），保留 data、浏览器、采集组件及可选语音组件。

更新前检查程序、基础环境、更新器和数据库要求；下载和校验可以在应用运行时完成。安装时安全停止任务及接收连接，完成后等待新版健康启动。若启动检查失败，恢复本次更新前的程序。若基础环境或数据库不满足要求，安装前会说明原因，不会先替换程序再尝试启动。

release-manifest.json 记录源码提交、工作区是否有未提交修改、构建时间和数据库要求，便于确认某个 ZIP 对应的实际源码。

首次打开后，请进入“设置” > “授权与设备”，填写产品授权码并校验。
