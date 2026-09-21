# -*- coding: utf-8 -*-
"""渲染逻辑改动后的冒烟测试（只读图标、写临时目录，不碰注册表/光标）"""
import os, sys, struct, tempfile, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import cursor_tools as ct

tmp = tempfile.mkdtemp(prefix="petcursor_test_")
passed = []

def check(name, cond):
    passed.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)

icon = os.path.join(BASE, "icons", "1f98b.png")
ok = True
# 1) 各 scale 渲染不崩、尺寸正确
for sc in (0.3, 0.5, 1.0, 1.8, 2.5):
    for hs in ("topleft", "topcenter", "bottomleft", "bottomcenter", "center"):
        img = ct.render_cursor_image(icon, size=48, hotspot=hs, tip="ff8800", scale=sc)
        ok = ok and img.size == (48, 48)
check("render 各 scale/hotspot 不崩且尺寸正确", ok)

# 2) tip=0 也能渲染
img = ct.render_cursor_image(icon, size=48, hotspot="topleft", tip="0", scale=1.0)
check("tip=0 渲染正常", img.size == (48, 48))

# 3) make_cur 生成 CUR 且热点字段正确
p = os.path.join(tmp, "a.cur")
ct.make_cur(icon, p, size=32, hotspot="topleft", tip="ff8800", scale=1.0)
data = open(p, "rb").read()
check("make_cur 生成文件", os.path.exists(p) and len(data) > 22)
check("CUR 类型字段=2", struct.unpack_from("<H", data, 2)[0] == 2)
hx, hy = struct.unpack_from("<HH", data, 10)
check("热点字段正确 (0,0)", (hx, hy) == (0, 0))

# 4) build_cursors 生成 15 个角色文件
outd = os.path.join(tmp, "curs")
m = ct.build_cursors(icon, "test", size=32, hotspot="topleft", tip="ff8800", scale=1.0)
check("build_cursors 15 个角色", len(m) == 15 and all(os.path.exists(v) for v in m.values()))
check("文件名带 v2 版本", all("-v2" in os.path.basename(v) for v in m.values()))

# 5) 小图案时箭头紧贴图案：用纯红方块做图案（箭头是白+黑描边，颜色可区分），
#    红色像素沿箭身方向到热点的最近投影应 ≈ show_len
from PIL import Image
Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(os.path.join(tmp, "red.png"))
small = ct.render_cursor_image(os.path.join(tmp, "red.png"), size=48,
                               hotspot="topleft", tip="ffffff", scale=0.35)
a = small.getchannel("A")
w, h = small.size
ux, uy = 0.70710678, 0.70710678
best = None
for i, v in enumerate(a.getdata()):
    r, g, b, _ = small.getpixel((i % w, i // w))
    if v > 200 and r > 200 and g < 80 and b < 80:   # 只看红色方块像素
        d = (i % w) * ux + (i // w) * uy
        best = d if best is None else min(best, d)
L = max(7.0, 48 * 0.26)
keep = L * 0.55 + max(2.0, 48 * 0.05)   # 箭头露出 + 一道小缝
check("小图案可见内容在箭头尖外侧留缝 (gap≈keep)",
      best is not None and abs(best - keep) < 1.6)

# 6) server.py 可正常导入
import importlib
srv = importlib.import_module("server")
check("server 导入无异常", srv is not None)

shutil.rmtree(tmp, ignore_errors=True)
fails = [n for n, c in passed if not c]
print(f"\n{len(passed) - len(fails)}/{len(passed)} 通过")
sys.exit(1 if fails else 0)
