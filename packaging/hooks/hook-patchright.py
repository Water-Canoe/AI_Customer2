from PyInstaller.utils.hooks import collect_all


# 发布器统一使用完整 Chromium，不打包重复的 headless shell。
datas, binaries, hiddenimports = collect_all("patchright")
datas = [item for item in datas if "chromium_headless_shell" not in str(item[0])]
binaries = [item for item in binaries if "chromium_headless_shell" not in str(item[0])]
