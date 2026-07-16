from PyInstaller.utils.hooks import collect_data_files


# 异步入口与同步入口共用同一份官方Playwright驱动。
datas = collect_data_files("playwright")
