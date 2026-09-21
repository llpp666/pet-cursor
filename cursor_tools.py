# -*- coding: utf-8 -*-
"""
Windows 光标工具库:
  - 把 PNG 转成带热点的 .cur 文件
  - 写入 HKCU\\Control Panel\\Cursors 并广播 SPI_SETCURSORS 使其立即生效
  - 备份 / 恢复系统默认光标
"""
import ctypes
import io
import json
import math
import os
import struct
import sys
import threading
import time

from PIL import Image, ImageDraw

try:
    import winreg
except ImportError:  # 非 Windows（仅用于开发调试）
    winreg = None

APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "PetCursor"
)
CUR_DIR = os.path.join(APP_DIR, "cursors")
BACKUP_FILE = os.path.join(APP_DIR, "backup.json")

CUR_KEY = r"Control Panel\Cursors"
GUARD_FILE = os.path.join(APP_DIR, "guard.json")

# 注册表值名 -> 中文说明
ROLES = [
    ("Arrow", "正常选择（主箭头）"),
    ("Hand", "链接选择（小手）"),
    ("IBeam", "文本选择（I 型）"),
    ("Crosshair", "精确选择（十字）"),
    ("AppStarting", "后台运行"),
    ("Wait", "忙（沙漏）"),
    ("No", "不可用"),
    ("Help", "帮助选择"),
    ("SizeNS", "垂直调整"),
    ("SizeWE", "水平调整"),
    ("SizeNWSE", "对角调整 1"),
    ("SizeNESW", "对角调整 2"),
    ("SizeAll", "移动"),
    ("UpArrow", "候选"),
    ("NWPen", "手写笔"),
]

SPI_SETCURSORS = 0x0057
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002

HOTSPOTS = {
    "topleft": lambda s: (0, 0),
    "topcenter": lambda s: (s // 2, 0),
    "center": lambda s: (s // 2, s // 2),
    "bottomleft": lambda s: (0, s - 1),
    "bottomcenter": lambda s: (s // 2, s - 1),
}

# 箭头尖端的朝向（尖端指向画布外，图案往反方向让位）
TIP_DIRS = {
    "topleft": (-1, -1),
    "topcenter": (0, -1),
    "bottomleft": (-1, 1),
    "bottomcenter": (0, 1),
    "center": (-1, -1),
}

DEFAULT_WIN_CURSORS = {
    "Arrow": r"%SystemRoot%\cursors\aero_arrow",
    "Help": r"%SystemRoot%\cursors\aero_helpsel",
    "AppStarting": r"%SystemRoot%\cursors\aero_working",
    "Wait": r"%SystemRoot%\cursors\aero_busy",
    "Crosshair": "",
    "IBeam": "",
    "NWPen": r"%SystemRoot%\cursors\aero_pen",
    "No": r"%SystemRoot%\cursors\aero_unavail",
    "SizeNS": r"%SystemRoot%\cursors\aero_ns",
    "SizeWE": r"%SystemRoot%\cursors\aero_ew",
    "SizeNWSE": r"%SystemRoot%\cursors\aero_nwse",
    "SizeNESW": r"%SystemRoot%\cursors\aero_nesw",
    "SizeAll": r"%SystemRoot%\cursors\aero_move",
    "UpArrow": r"%SystemRoot%\cursors\aero_up",
    "Hand": r"%SystemRoot%\cursors\aero_link",
    "Scheme Source": "0",
}


# ---------------------------------------------------------------- 渲染
def _hex_rgb(value):
    """'#ff8800' / 'ff8800' -> (255,136,0)；非法值回退白色"""
    s = (value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        if len(s) != 6:
            raise ValueError
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def parse_tip(value):
    """tip 参数解析：True / '0' / 'ff8800' -> (是否显示, '#rrggbb')"""
    if value is None or value is True:
        return True, "#ffffff"
    if value is False:
        return False, "#ffffff"
    s = str(value).strip()
    if s.lower() in ("0", "false", "none", "no", ""):
        return False, "#ffffff"
    return True, "#" + s.lstrip("#")


def _draw_tip(canvas, hx, hy, dx, dy, size, color="#ffffff", L=None):
    """在热点处画一个实心三角箭头尖，尖端顶在热点上；描边自动取对比色"""
    fill = _hex_rgb(color)
    r, g, b = fill
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    outline = (0, 0, 0, 255) if lum > 140 else (255, 255, 255, 255)

    if L is None:
        L = max(8.0, size * 0.36)     # 三角长度
    W = L * 0.92                      # 底边宽
    nx, ny = -dy, dx                  # 法向

    poly = [
        (hx, hy),
        (hx + dx * L + nx * W / 2, hy + dy * L + ny * W / 2),
        (hx + dx * L - nx * W / 2, hy + dy * L - ny * W / 2),
    ]

    # 沿质心外扩一圈，作为黑色描边，保证在任何背景上都看得见
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    grown = []
    for px, py in poly:
        vx, vy = px - cx, py - cy
        n = math.hypot(vx, vy) or 1.0
        grown.append((px + vx / n * 1.5, py + vy / n * 1.5))

    d = ImageDraw.Draw(canvas)
    d.polygon(grown, fill=outline)
    d.polygon(poly, fill=fill + (255,))


def _min_proj(img, ux, uy):
    """内容图里不透明像素沿 (ux,uy) 方向的最小投影（内容图自身像素坐标）"""
    a = img.getchannel("A")
    w, _h = img.size
    best = None
    for i, v in enumerate(a.getdata()):
        if v > 16:
            d = (i % w) * ux + (i // w) * uy
            if best is None or d < best:
                best = d
    return best if best is not None else 0.0


def render_cursor_image(png_path, size=32, hotspot="topleft", tip=True,
                        tip_color="#ffffff", scale=1.0):
    """把图标渲染成光标图案

    scale: 图案在光标画布内的相对大小（可调 0.3~2.5）
    图案**始终紧贴热点**：不管图案多大/多小，箭头尖都顶在真正的点击点上、
    并且紧挨着图案边缘，不会出现「图案一小，箭头就离得老远」的情况。
    箭头画在底层、图案画在上层：只露出紧贴图案的那一小截尖端，箭身被图案压住，
    所以三角形永远不会遮挡图标。
    """
    if isinstance(tip, str):          # tip 直接传颜色字符串也行，如 "ff8800" / "0"
        show_tip, tip_color = parse_tip(tip)
    else:
        show_tip = bool(tip)
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    scale = max(0.3, min(2.5, scale))

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    src = Image.open(png_path).convert("RGBA")
    # 裁掉四周透明边距：emoji 图片常有大量空白，不裁的话
    # 图案"看着"离箭头很远（其实空隙全是图片自身的透明边）
    # 只按 Alpha 裁剪；透明像素里的 RGB 残留不应制造隐形边距。
    alpha = src.getchannel("A").point(lambda value: 255 if value > 16 else 0)
    bbox = alpha.getbbox()
    if bbox:
        src = src.crop(bbox)
    iw, ih = src.size

    dx, dy = TIP_DIRS.get(hotspot, TIP_DIRS["topleft"])
    m = math.hypot(dx, dy) or 1.0
    dx, dy = dx / m, dy / m
    ux, uy = -dx, -dy                # 箭身方向（从热点指向画布内 / 图案那一侧）

    hx, hy = HOTSPOTS.get(hotspot, HOTSPOTS["topleft"])(size)

    s = max(4, min(size, int(round(size * scale))))

    if show_tip:
        # 箭头大小固定（不随图案缩放），图案再大尖端也完整露在外面
        L = max(7.0, size * 0.26)
        show_len = L * 0.55          # 露在图案外面的那一截（尖端 + 小半截箭身）
        pad = max(2.0, size * 0.05)  # 箭头尖与图案之间的缝隙：贴得很近但不粘在一起
        keep = show_len + pad        # 图案可见部分到热点的距离
    else:
        L = show_len = pad = keep = 0.0

    def _fit(side):
        """等比缩放内容到边长 side 的方框内，返回实际内容宽高"""
        k = side / max(iw, ih)
        return max(1, round(iw * k)), max(1, round(ih * k))

    def _place(side):
        """把内容盒摆到「紧贴热点」的位置，返回内容盒左上角坐标"""
        cw, ch = _fit(side)
        proj = (cw / 2.0) * abs(ux) + (ch / 2.0) * abs(uy)   # 中心沿箭身方向到近端的投影
        return (hx + ux * (keep + proj) - cw / 2.0,
                hy + uy * (keep + proj) - ch / 2.0, cw, ch)

    if show_tip and hotspot != "center":
        x, y, cw, ch = _place(s)
        while s > 4:
            img = src.resize((cw, ch), Image.LANCZOS)
            # 像素级定位：图案可见部分（非透明像素）沿箭身方向到热点的最近距离，
            # 调整到 keep —— 即箭头尖外侧留 pad 的一道小缝，贴得很近但不粘住
            gap = _min_proj(img, ux, uy) + (x - hx) * ux + (y - hy) * uy
            if abs(gap - keep) > 0.01:
                step = keep - gap
                x += ux * step
                y += uy * step
            if (x >= -0.5 and x + cw <= size + 0.5
                    and y >= -0.5 and y + ch <= size + 0.5):
                break
            # 推近后放不下（会被画布裁掉）→ 缩小一档重来
            s -= 1
            x, y, cw, ch = _place(s)
        _draw_tip(canvas, hx, hy, ux, uy, size, color=tip_color, L=L)
    else:
        # 不显示箭头（或热点在正中间）时，内容居中摆放
        cw, ch = _fit(s)
        img = src.resize((cw, ch), Image.LANCZOS)
        x, y = (size - cw) / 2.0, (size - ch) / 2.0
        if show_tip:
            _draw_tip(canvas, hx, hy, ux, uy, size, color=tip_color, L=L)

    x = int(round(max(0, min(size - cw, x))))
    y = int(round(max(0, min(size - ch, y))))
    canvas.alpha_composite(img, (x, y))
    return canvas


# ---------------------------------------------------------------- 生成 .cur
def make_cur(png_path, out_path, size=32, hotspot="topleft", tip=True,
             tip_color="#ffffff", scale=1.0):
    """PNG -> Windows .cur（在 ICO 基础上改写 type 与热点字段）"""
    img = render_cursor_image(png_path, size=size, hotspot=hotspot,
                              tip=tip, tip_color=tip_color, scale=scale)
    buf = io.BytesIO()
    img.save(buf, format="ICO", sizes=[(size, size)])
    data = bytearray(buf.getvalue())

    if len(data) < 22:
        raise RuntimeError("ICO 数据异常")

    struct.pack_into("<H", data, 2, 2)  # ICONDIR.type: 1=ICO -> 2=CUR
    hx, hy = HOTSPOTS.get(hotspot, HOTSPOTS["topleft"])(size)
    struct.pack_into("<H", data, 10, hx)  # ICONDIRENTRY.wPlanes  -> hotspot X
    struct.pack_into("<H", data, 12, hy)  # ICONDIRENTRY.wBitCount -> hotspot Y

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(bytes(data))
    return out_path


def build_cursors(icon_path, stem, size=32, hotspot="topleft", tip=True,
                  tip_color="#ffffff", scale=1.0):
    """为同一图标生成各角色的 .cur，返回 {role: abs_path}"""
    show_tip, color = parse_tip(tip) if isinstance(tip, str) else (bool(tip), tip_color)
    tag = (color.lstrip("#") if show_tip else "plain") + "-v2"
    out = {}
    for role, _label in ROLES:
        p = os.path.join(
            CUR_DIR, f"{stem}_{role}_{size}_{hotspot}_{tag}_{scale:g}.cur"
        )
        make_cur(icon_path, p, size=size, hotspot=hotspot, tip=tip,
                 tip_color=tip_color, scale=scale)
        out[role] = p
    return out


# ---------------------------------------------------------------- 注册表
def _open_key(write=False):
    if winreg is None:
        raise RuntimeError("仅支持 Windows")
    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE if write else winreg.KEY_QUERY_VALUE
    try:
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, CUR_KEY, 0, access)
    except OSError:
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, CUR_KEY, 0, access)


def read_current():
    """读取当前光标设置（用于备份）"""
    cur = {}
    if winreg is None:
        return cur
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, CUR_KEY, 0, winreg.KEY_QUERY_VALUE)
    except OSError:
        return cur
    i = 0
    while True:
        try:
            name, value, _type = winreg.EnumValue(k, i)
        except OSError:
            break
        cur[name] = value
        i += 1
    winreg.CloseKey(k)
    return cur


def backup():
    """首次修改前备份当前设置；已备份则保留最早那份"""
    if os.path.exists(BACKUP_FILE):
        return False
    os.makedirs(APP_DIR, exist_ok=True)
    data = read_current()
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump({"cursors": data}, f, ensure_ascii=False, indent=2)
    return True


def apply_cursors(cur_map, roles, guard=True):
    """只替换 roles 中列出的角色"""
    if winreg is None:
        raise RuntimeError("仅支持 Windows")
    backup()
    k = _open_key(write=True)
    for role in roles:
        path = cur_map.get(role)
        if not path:
            continue
        winreg.SetValueEx(k, role, 0, winreg.REG_EXPAND_SZ, path)
    winreg.SetValueEx(k, "Scheme Source", 0, winreg.REG_DWORD, 1)
    winreg.CloseKey(k)
    broadcast()
    if guard:
        guard_start(cur_map, roles)
    return True


def broadcast():
    ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETCURSORS, 0, None, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    )


def restore():
    """恢复：优先用备份，其次写回 Windows 默认方案"""
    if winreg is None:
        raise RuntimeError("仅支持 Windows")
    guard_stop()          # 用户主动恢复默认，守护必须同时停掉，否则会立刻被写回来
    k = _open_key(write=True)
    if os.path.exists(BACKUP_FILE):
        with open(BACKUP_FILE, encoding="utf-8") as f:
            saved = json.load(f).get("cursors", {})
        # 先清掉我们可能写入的角色
        for role, _l in ROLES:
            try:
                winreg.DeleteValue(k, role)
            except OSError:
                pass
        for name, value in saved.items():
            if isinstance(value, int):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_EXPAND_SZ, value)
    else:
        for name, value in DEFAULT_WIN_CURSORS.items():
            if name == "Scheme Source":
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0)
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_EXPAND_SZ, value)
    winreg.CloseKey(k)
    broadcast()


# ---------------------------------------------------------------- 光标守护
# Windows 在右键菜单、资源管理器重启、UAC 弹窗、远程桌面等场景会临时把光标
# 换回系统默认（表现为"闪一下默认箭头，几秒后才回来"）。守护线程持续比对注册表，
# 一旦被改回默认就立刻重写并广播，把恢复时间压到 1~2 秒内。
_guard_thread = None
_guard_lock = threading.Lock()


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p or "")))


def guard_state():
    try:
        with open(GUARD_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            return st
    except Exception:
        pass
    return {"on": False, "map": {}}


def _guard_save(st):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = GUARD_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, GUARD_FILE)
    except OSError:
        pass


def guard_start(cur_map, roles, meta=None):
    """记录本次应用的光标，并开启守护"""
    st = {
        "on": True,
        "map": {r: cur_map[r] for r in roles if cur_map.get(r)},
        "meta": meta or {},
        "hits": 0,
        "since": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _guard_save(st)
    guard_thread_start()
    return st


def guard_stop():
    st = guard_state()
    st["on"] = False
    _guard_save(st)
    return st


def guard_set(on):
    """开关守护；开启时会沿用上次记录的光标"""
    st = guard_state()
    if on and st.get("map"):
        return guard_start(st["map"], list(st["map"].keys()), st.get("meta"))
    return guard_stop()


def _guard_tick():
    """检查一次；返回 True 表示刚才被系统改回默认、已重新写回"""
    st = guard_state()
    if not st.get("on") or not st.get("map"):
        return False
    cur = read_current()
    bad = [r for r, p in st["map"].items() if _norm(cur.get(r)) != _norm(p)]
    if not bad:
        return False
    if winreg is None:
        return False
    try:
        k = _open_key(write=True)
        for r in bad:
            k.SetValueEx(r, 0, winreg.REG_EXPAND_SZ, st["map"][r])
        k.SetValueEx("Scheme Source", 0, winreg.REG_DWORD, 1)
        k.CloseKey()
    except OSError:
        return False
    broadcast()
    st["hits"] = st.get("hits", 0) + 1
    st["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st["last_fixed"] = bad
    _guard_save(st)
    return True


def _guard_loop(interval=1.5):
    while True:
        try:
            _guard_tick()
        except Exception:
            pass
        time.sleep(interval)


def guard_thread_start(interval=1.5):
    """启动守护线程（进程内只起一个）"""
    global _guard_thread
    with _guard_lock:
        if _guard_thread and _guard_thread.is_alive():
            return _guard_thread
        _guard_thread = threading.Thread(target=_guard_loop, args=(interval,),
                                         daemon=True, name="cursor-guard")
        _guard_thread.start()
        return _guard_thread


def guard_info():
    st = guard_state()
    return {
        "on": bool(st.get("on")),
        "roles": len(st.get("map", {})),
        "hits": st.get("hits", 0),
        "last": st.get("last", ""),
        "alive": bool(_guard_thread and _guard_thread.is_alive()),
    }


def status():
    """返回当前系统箭头光标设置，供 UI 显示"""
    cur = read_current()
    return {
        "arrow": cur.get("Arrow", "(系统默认)"),
        # 只有指向本工具生成的 .cur 才算"已自定义"，系统默认路径不算
        "customized": any(str(cur.get(r, "")).startswith(CUR_DIR) for r, _l in ROLES),
        "has_backup": os.path.exists(BACKUP_FILE),
        "backup_file": BACKUP_FILE,
        "app_dir": APP_DIR,
        "guard": guard_info(),
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        restore()
        print("已恢复系统默认光标")
    elif len(sys.argv) > 1 and sys.argv[1] == "guard":
        # 纯守护模式：不启服务、不开窗口，只在后台守住光标
        guard_thread_start()
        print("光标守护已启动，按 Ctrl+C 结束")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
