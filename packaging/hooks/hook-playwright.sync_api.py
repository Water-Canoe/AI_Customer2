from PyInstaller.utils.hooks import collect_data_files


# 只收集官方Playwright驱动，避免环境中的Patchright钩子误注入。
datas = collect_data_files("playwright")
