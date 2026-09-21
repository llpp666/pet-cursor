# -*- coding: utf-8 -*-
"""打一个可以直接发到 GitHub Releases 的 zip。

用法：
    python make_release.py              # 先 pyinstaller 小虫光标.spec，再跑这个

产物：release_pkg/PetCursor-Windows.zip

⚠️ 发布包里绝不能带私人数据：本脚本会显式排除 user_icons/ 与 小蝴蝶编号.json，
   以免把自己上传的图、自己的下载计数一起分发出去。

⚠️ zip 文件名用 ASCII：GitHub Releases 上传中文文件名的 asset 会把中文吃掉
   （实测 `小虫光标-Windows.zip` 变成 `-Windows.zip`），所以对外分发一律用英文名的 zip，
   里面的文件夹仍可以是中文。
"""
import os
import shutil
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
# 打包输出目录可用环境变量覆盖（默认 dist）：dist 目录被占用/锁定时，
# 用 --distpath dist2 打包后设 PETCURSOR_DIST_DIR=dist2 再跑本脚本
SRC = os.path.join(BASE, os.environ.get("PETCURSOR_DIST_DIR", "dist"), "小虫光标")
OUT_DIR = os.path.join(BASE, os.environ.get("PETCURSOR_RELEASE_DIR", "release_pkg"))
PKG = os.path.join(OUT_DIR, "小虫光标")
ZIP = os.path.join(OUT_DIR, "PetCursor-Windows.zip")

# 放进发布包的附加文件（相对 BASE）
EXTRA = ["启动工具.bat", "community.example.json", "使用说明.txt",
         "README.md", "LICENSE"]

# 绝不分发：私人图片目录、下载计数、缓存、日志
EXCLUDE_DIRS = {"user_icons", "private_icons", "__pycache__", "logs"}
EXCLUDE_FILES = {"小蝴蝶编号.json", "community.json", "private_lock.json"}


def main():
    if not os.path.isdir(SRC):
        print("找不到 %s，请先运行：pyinstaller 小虫光标.spec" % SRC)
        return 1

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(PKG, exist_ok=True)

    # 1) 复制 exe 与 _internal
    n = 0
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel = os.path.relpath(root, SRC)
        dst = PKG if rel == "." else os.path.join(PKG, rel)
        os.makedirs(dst, exist_ok=True)
        for f in files:
            if f in EXCLUDE_FILES:
                continue
            shutil.copy2(os.path.join(root, f), os.path.join(dst, f))
            n += 1

    # 2) 附加文件
    for f in EXTRA:
        p = os.path.join(BASE, f)
        if os.path.exists(p):
            shutil.copy2(p, PKG)
            n += 1

    # 3) 自检：确认没有私人数据混进来
    leaked = []
    for root, dirs, files in os.walk(PKG):
        for d in dirs:
            if d in ("user_icons", "private_icons"):
                leaked.append(os.path.join(root, d))
        for f in files:
            if f in EXCLUDE_FILES:
                leaked.append(os.path.join(root, f))
    if leaked:
        print("❌ 发布包里发现了不该有的东西，已中止：")
        for x in leaked:
            print("   ", x)
        return 2

    # 4) 压缩
    total = 0
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(PKG):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.join("小虫光标", os.path.relpath(p, PKG)))
                total += 1

    raw = sum(os.path.getsize(os.path.join(r, f))
              for r, _, fs in os.walk(PKG) for f in fs)
    print("✅ 发布包已生成")
    print("   文件数   %d" % total)
    print("   原始大小 %.1f MB" % (raw / 1048576))
    print("   压缩后   %.1f MB" % (os.path.getsize(ZIP) / 1048576))
    print("   路径     %s" % ZIP)
    print()
    print("解压后双击「启动工具.bat」即可使用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
