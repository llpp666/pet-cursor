# -*- coding: utf-8 -*-
"""把小虫光标打包成 Windows 桌面软件（dist\\小虫光标\\小虫光标.exe）"""
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

APP = "小虫光标"
DIST = os.path.join(BASE, "发布")
WORK = os.path.join(BASE, "build_release")

# 模型直接放进发布包，用户第一次抠图也不依赖网络。
from matting import _bundled_model_path  # noqa: E402

AI_MODEL = _bundled_model_path(download=True)

# 先关掉可能占用端口的旧进程（忽略失败）
try:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$c = Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue; "
         "if ($c) { $c | Select-Object -ExpandProperty OwningProcess -Unique | "
         "ForEach-Object { Stop-Process -Id $_ -Force -Confirm:$false } }"],
        capture_output=True, timeout=60,
    )
except Exception:
    pass

args = [
    "app.py",
    "--name", APP,
    "--noconfirm",
    "--noconsole",
    "--onedir",
    "--icon", os.path.join(BASE, "app.ico"),
    "--distpath", DIST,
    "--workpath", WORK,
    "--specpath", BASE,
    # rembg 会带入 scipy/numba 等重依赖；发布包只内置 matting.py 使用的
    # NumPy + ONNX Runtime 轻量推理路径，模型首次使用时下载约 5MB。
    "--exclude-module", "rembg",
    "--exclude-module", "numba",
    "--exclude-module", "llvmlite",
    "--exclude-module", "pymatting",
    "--exclude-module", "scipy",
    "--exclude-module", "skimage",
    "--hidden-import", "numpy",
    "--hidden-import", "onnxruntime",
    "--collect-all", "PIL",
    "--add-data", "web;web",
    "--add-data", "catalog.json;.",
    "--add-data", "icons;icons",
    "--add-data", AI_MODEL + ";models",
    "--exclude-module", "tkinter",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PySide2",
    "--exclude-module", "matplotlib",
]

import PyInstaller.__main__  # noqa: E402

PyInstaller.__main__.run(args)

out = os.path.join(DIST, APP)
print("BUILD DONE ->", out)

# PyInstaller 可能从构建环境收集到旧版 MSVCP140.dll，并和新版
# VCRUNTIME140.dll 混装；ONNX Runtime 导入时会因此以 0xC0000005 崩溃。
# 三个运行库必须来自同一套已安装的 VC++ Runtime。
if os.name == "nt":
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    internal = os.path.join(out, "_internal")
    for dll in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        source = os.path.join(system32, dll)
        target = os.path.join(internal, dll)
        if os.path.isfile(source) and os.path.isdir(internal):
            shutil.copy2(source, target)
            print("synced VC runtime:", dll)

# 把已上传的图片带到发布目录
src_icons = os.path.join(BASE, "user_icons")
dst_icons = os.path.join(out, "user_icons")
if os.path.isdir(src_icons):
    os.makedirs(dst_icons, exist_ok=True)
    for f in os.listdir(src_icons):
        s, d = os.path.join(src_icons, f), os.path.join(dst_icons, f)
        if os.path.isfile(s) and not os.path.exists(d):
            shutil.copy2(s, d)
    print("copied user_icons:", len(os.listdir(dst_icons)), "files")

size = sum(
    os.path.getsize(os.path.join(r, f))
    for r, _d, fs in os.walk(out) for f in fs
)
print("folder size: %.0f MB" % (size / 1024 / 1024))
