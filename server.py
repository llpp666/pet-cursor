# -*- coding: utf-8 -*-
"""
小虫光标 · Pet Cursor —— 本地服务
启动后浏览器打开 http://127.0.0.1:8899

只监听本机 127.0.0.1，局域网里的其他设备访问不到。
公共区图片存在软件目录（跟着软件文件夹走），私人区图片存在当前 Windows 账户目录（别人拷不走）。
无需注册登录，打开就能用。
"""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# 打包成 exe 后：内置资源在 sys._MEIPASS，用户上传的图片写到 exe 同级目录（可写）
FROZEN = getattr(sys, "frozen", False)
DATA_DIR = getattr(sys, "_MEIPASS", BASE) if FROZEN else BASE
USER_DIR = os.environ.get("PETCURSOR_USER_DIR") or (
    os.path.join(os.path.dirname(sys.executable), "user_icons") if FROZEN
    else os.path.join(BASE, "user_icons")
)

import butterfly  # noqa: E402
import community  # noqa: E402
import cursor_tools  # noqa: E402
import matting  # noqa: E402
from PIL import Image  # noqa: E402

WEB_DIR = os.path.join(DATA_DIR, "web")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

# 小蝴蝶编号记在软件目录里（跟着软件文件夹走，拷给别人就 +1）
butterfly.set_counter_dir(os.path.dirname(USER_DIR))

# ---------------------------------------------------------------- 图片分区
# 公共区：程序目录里的 user_icons，跟着软件文件夹走 —— 谁拿到这个文件夹谁都能用
# 私人区：当前 Windows 账户目录 —— 只存在这台电脑上，别人拷不走
SHARED_DIR = USER_DIR
LOCAL_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PetCursor")
PRIVATE_DIR = os.path.join(LOCAL_DIR, "private_icons")
SHARED_CATALOG = os.path.join(SHARED_DIR, "catalog.json")
PRIVATE_CATALOG = os.path.join(PRIVATE_DIR, "catalog.json")
PORT = 8899


# ---------------------------------------------------------------- 开机守护
def autostart_path():
    """(开机启动目录, 快捷方式是否已存在)"""
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                     r"Microsoft\Windows\Start Menu\Programs\Startup")
    lnk = os.path.join(d, "小虫光标守护.lnk")
    return lnk, os.path.exists(lnk)


def guard_cmd():
    """后台守护模式的启动命令（不弹窗口）"""
    if FROZEN:
        return sys.executable, "--guard-only"
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    return pyw, '"%s" --guard-only' % os.path.join(BASE, "app.py")


def set_autostart(on):
    """在开机启动目录里放/删一个快捷方式 —— 只在用户主动点击时才调用"""
    lnk, exists = autostart_path()
    if not on:
        if exists:
            try:
                os.remove(lnk)
            except OSError:
                return False, "删不掉旧快捷方式，手动删一下：%s" % lnk
        return False, "已关闭"
    parent = os.path.dirname(lnk)
    if not os.path.isdir(parent):
        return False, "找不到开机启动目录"
    target, args = guard_cmd()
    ps = (
        '$s=New-Object -ComObject WScript.Shell;'
        '$l=$s.CreateShortcut("%s");'
        '$l.TargetPath="%s";'
        '$l.Arguments=%s;'
        '$l.Description="小虫光标 光标守护";'
        '$l.WindowStyle=7;'
        '$l.Save()'
    ) % (lnk.replace('"', '`"'), target.replace('"', '`"'),
         "'" + args.replace("'", "''") + "'")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=25,
        )
    except Exception as e:
        return False, "调用 PowerShell 失败：%s" % e
    if r.returncode != 0:
        msg = (r.stderr or b"").decode("utf-8", "ignore")[:200]
        return False, "创建快捷方式失败：%s" % (msg or r.returncode)
    return os.path.exists(lnk), "已开启" if os.path.exists(lnk) else "创建失败"


def dir_for(scope):
    return PRIVATE_DIR if scope == "private" else SHARED_DIR


def prefix_for(scope):
    return "private_icons/" if scope == "private" else "user_icons/"


def icon_path(item):
    # 社区图标：真实文件在缓存目录，路径已在同步时算好
    if item.get("_abs"):
        return item["_abs"]
    f = item.get("file", "")
    if item.get("scope") == "private" or "/private_icons/" in f:
        return os.path.join(PRIVATE_DIR, os.path.basename(f))
    if f.startswith("user_icons/") or "/user_icons/" in f:
        return os.path.join(SHARED_DIR, os.path.basename(f))
    return os.path.join(DATA_DIR, f)


def load_catalog():
    with open(CATALOG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _load_cat_file(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("items", []) if isinstance(data, dict) else data
    except Exception:
        return []


def _save_cat_file(path, items):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)


def cat_file_for(scope):
    return PRIVATE_CATALOG if scope == "private" else SHARED_CATALOG


def load_user_items():
    """公共区 + 私人区"""
    os.makedirs(SHARED_DIR, exist_ok=True)
    os.makedirs(PRIVATE_DIR, exist_ok=True)
    out = []
    for it in _load_cat_file(SHARED_CATALOG):
        it = dict(it)
        it["scope"] = "public"
        out.append(it)
    for it in _load_cat_file(PRIVATE_CATALOG):
        it = dict(it)
        it["scope"] = "private"
        out.append(it)
    return out


def save_user_items(items):
    _save_cat_file(SHARED_CATALOG, [i for i in items if i.get("scope") != "private"])
    _save_cat_file(PRIVATE_CATALOG, [i for i in items if i.get("scope") == "private"])


def migrate_account_private():
    """旧版本把私人图片按账号分目录存放，统一搬回私人区根目录"""
    if not os.path.isdir(PRIVATE_DIR):
        return 0
    items = _load_cat_file(PRIVATE_CATALOG)
    have = {os.path.basename(i.get("file", "")) for i in items}
    moved = 0
    for name in os.listdir(PRIVATE_DIR):
        sub = os.path.join(PRIVATE_DIR, name)
        if not os.path.isdir(sub):
            continue
        for it in _load_cat_file(os.path.join(sub, "catalog.json")):
            fn = os.path.basename(it.get("file", ""))
            src, dst = os.path.join(sub, fn), os.path.join(PRIVATE_DIR, fn)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.move(src, dst)
            it = dict(it)
            it["scope"] = "private"
            it.pop("owner", None)
            it["file"] = "private_icons/" + fn
            if fn not in have:
                items.append(it)
                have.add(fn)
                moved += 1
        shutil.rmtree(sub, ignore_errors=True)
    if moved:
        _save_cat_file(PRIVATE_CATALOG, items)
    return moved


CATALOG = load_catalog()
migrate_account_private()      # 旧版本按账号分目录的私人图片收进统一私人区

# 后台拉一次社区图标库（没配仓库 / 没网都会静默跳过，不影响启动）
community.start_background_check()

# 进程一启动就接管光标守护（如果上次应用过并开着守护）
try:
    if cursor_tools.guard_state().get("on"):
        cursor_tools.guard_thread_start()
except Exception:
    pass


def items_for():
    """内置图标库 + 社区图标库（去重）+ 用户上传的公共/私人图"""
    out = list(CATALOG["items"])
    known = {it.get("id") for it in out}
    for it in community.community_items():
        if it.get("id") not in known:
            out.append(it)
            known.add(it.get("id"))
    return out + load_user_items()


def public_items_for_frontend(items):
    """去掉内部的绝对路径再发给前端"""
    return [{k: v for k, v in it.items() if k != "_abs"} for it in items]


def index_for():
    return {it["id"]: it for it in items_for()}



class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静音访问日志

    # ------------------------------------------------------------ helpers
    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _file(self, path, ctype):
        if not os.path.exists(path):
            self._send(404, b"not found", "text/plain")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _lan_ok(self, path):
        # 只服务本机访问：局域网里的其他设备一律拒绝，改不了你的鼠标
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    # ------------------------------------------------------------ routes
    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)

        if not self._lan_ok(p):
            return self._send(403, b"forbidden", "text/plain")

        if p in ("/", "/index.html"):
            return self._file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")

        if p == "/api/catalog":
            its = items_for()
            return self._json({
                "items": public_items_for_frontend(its),
                "license": CATALOG["license"],
                "roles": [{"key": k, "label": v} for k, v in cursor_tools.ROLES],
                "dirs": {"public": SHARED_DIR, "private": PRIVATE_DIR},
                "community": community.status(),
            })

        if p == "/api/community/status":
            return self._json(community.status())

        if p == "/api/community/update":
            ok, msg = community.sync(force=True)
            st = community.status()
            st["msg"] = msg
            return self._json(st if ok else dict(st, ok=False))

        if p == "/api/status":
            try:
                st = cursor_tools.status()
                st["autostart"] = autostart_path()[1]
                return self._json({"ok": True, **st})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)

        # 小蝴蝶编号：软件每在一台新电脑上运行就 +1
        if p == "/api/butterfly":
            return self._json({"ok": True, **butterfly.info()})

        # 光标守护状态
        if p == "/api/guard":
            return self._json({"ok": True, **cursor_tools.guard_info(),
                               "autostart": autostart_path()[1]})

        if p == "/api/matting/status":
            ok, has_model, msg = matting.engine_status()
            return self._json({"ok": True, "engine": ok, "model": has_model, "msg": msg})

        if p.startswith("/api/preview/"):
            icon_id = p[len("/api/preview/"):].replace(".png", "")
            size = int(q.get("size", ["48"])[0])
            size = max(16, min(256, size))     # Windows 光标文件最大 256
            hotspot = q.get("hotspot", ["topleft"])[0]
            tip = q.get("tip", ["ffffff"])[0]
            scale = float(q.get("scale", ["1"])[0])
            it = index_for().get(icon_id)
            if not it:
                return self._send(404, b"no icon", "text/plain")
            img = cursor_tools.render_cursor_image(
                icon_path(it), size=size, hotspot=hotspot, tip=tip, scale=scale
            )
            b = io.BytesIO()
            img.save(b, "PNG")
            return self._send(200, b.getvalue(), "image/png")

        if p.startswith("/icons/"):
            return self._file(os.path.join(DATA_DIR, p.lstrip("/")), "image/png")
        if p.startswith("/web/"):
            # 打包成 exe 后静态资源在 _MEIPASS（_internal）里，不是 exe 同级目录
            f = os.path.join(DATA_DIR, p.lstrip("/"))
            ctype = "application/javascript; charset=utf-8" if f.endswith(".js") else "text/plain"
            self._file(f, ctype)
            return
        if p == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)

        if not self._lan_ok(p):
            return self._send(403, b"forbidden", "text/plain")

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}

        ICONS = index_for()

        if p == "/api/apply":
            try:
                icon_id = payload.get("icon_id")
                size = int(payload.get("size", 32))
                size = max(16, min(256, size))
                hotspot = payload.get("hotspot", "topleft")
                tip = payload.get("tip", True)
                scale = float(payload.get("scale", 1))
                roles = payload.get("roles") or ["Arrow", "Hand"]
                it = ICONS.get(icon_id)
                if not it:
                    return self._json({"ok": False, "error": "图标不存在"}, 400)
                src = icon_path(it)
                cur_map = cursor_tools.build_cursors(
                    src, icon_id, size=size, hotspot=hotspot, tip=tip, scale=scale
                )
                cursor_tools.apply_cursors(cur_map, roles)
                return self._json({
                    "ok": True,
                    "icon": it["name"],
                    "roles": roles,
                    "size": size,
                    "hotspot": hotspot,
                    "tip": tip,
                    "scale": scale,
                    "status": cursor_tools.status(),
                    "guard": cursor_tools.guard_info(),
                })
            except Exception as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

        # 光标守护开关 / 开机自动守护
        if p == "/api/guard":
            try:
                if payload.get("autostart") is not None:
                    ok, msg = set_autostart(bool(payload["autostart"]))
                    if not ok and payload["autostart"]:
                        return self._json({"ok": False, "error": msg}, 400)
                    return self._json({"ok": True, "msg": msg,
                                       **cursor_tools.guard_info(),
                                       "autostart": autostart_path()[1]})
                want = bool(payload.get("on", True))
                if want and not cursor_tools.guard_state().get("map"):
                    return self._json({"ok": False,
                                       "error": "还没应用过光标，先选一个图标点应用"}, 400)
                cursor_tools.guard_set(want)
                if want:
                    cursor_tools.guard_thread_start()
                return self._json({"ok": True, **cursor_tools.guard_info(),
                                   "autostart": autostart_path()[1]})
            except Exception as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

        if p == "/api/restore":
            try:
                cursor_tools.restore()
                return self._json({"ok": True, "status": cursor_tools.status(),
                                   "guard": cursor_tools.guard_info()})
            except Exception as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

        if p == "/api/matting":
            try:
                it = ICONS.get(payload.get("icon_id"))
                if not it:
                    return self._json({"ok": False, "error": "图标不存在"}, 400)
                src = Image.open(icon_path(it)).convert("RGBA")
                out = matting.to_square(matting.remove_bg(src))
                info = matting.last_info()      # 这次到底用的是 AI 还是简易去背景
                new_id = "u" + uuid.uuid4().hex[:8]
                name = it["name"][:12] + "·抠图"
                scope = it.get("scope", "public")
                out.save(os.path.join(dir_for(scope), new_id + ".png"), "PNG")
                item = {"id": new_id, "name": name, "emoji": "", "cat": "我的图片",
                        "scope": scope, "file": prefix_for(scope) + new_id + ".png"}
                items = load_user_items()
                items.append(item)
                save_user_items(items)
                ok, _m, msg = matting.engine_status()
                return self._json({"ok": True, "item": item, "engine": ok,
                                   "used_ai": info.get("ai", False),
                                   "model": info.get("model", ""),
                                   "msg": info.get("msg") or msg})
            except Exception as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

        if p == "/api/upload":
            try:
                raw = payload.get("data") or ""
                if "," in raw:
                    raw = raw.split(",", 1)[1]
                binary = base64.b64decode(raw)
                if len(binary) > 12 * 1024 * 1024:
                    return self._json({"ok": False, "error": "图片过大（>12MB）"}, 400)
                img = Image.open(io.BytesIO(binary))
                img.load()
                img = img.convert("RGBA")
                w, h = img.size
                if payload.get("fit", "contain") == "crop":
                    s = min(w, h)
                    img = img.crop(((w - s) // 2, (h - s) // 2,
                                    (w - s) // 2 + s, (h - s) // 2 + s))
                    img = img.resize((512, 512), Image.LANCZOS)
                else:
                    scale = 512 / max(w, h)
                    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
                    img = img.resize((nw, nh), Image.LANCZOS)
                    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
                    canvas.alpha_composite(img, ((512 - nw) // 2, (512 - nh) // 2))
                    img = canvas

                icon_id = "u" + uuid.uuid4().hex[:8]
                name = (payload.get("name") or "我的图片").strip()[:16] or "我的图片"
                scope = "private" if payload.get("scope") == "private" else "public"
                os.makedirs(dir_for(scope), exist_ok=True)
                img.save(os.path.join(dir_for(scope), icon_id + ".png"), "PNG")

                item = {"id": icon_id, "name": name, "emoji": "", "cat": "我的图片",
                        "scope": scope, "file": prefix_for(scope) + icon_id + ".png"}
                items = load_user_items()
                items.append(item)
                save_user_items(items)
                return self._json({"ok": True, "item": item})
            except Exception as e:
                return self._json({"ok": False, "error": f"无法识别这张图片：{e}"}, 400)

        if p == "/api/icon/scope":
            try:
                icon_id = payload.get("icon_id")
                scope = "private" if payload.get("scope") == "private" else "public"
                it = ICONS.get(icon_id)
                if not it or it.get("cat") != "我的图片":
                    return self._json({"ok": False, "error": "只能移动自己上传的图片"}, 400)
                if it.get("scope", "public") != scope:
                    src_fp = icon_path(it)
                    dst_fp = os.path.join(dir_for(scope), os.path.basename(it["file"]))
                    os.makedirs(os.path.dirname(dst_fp), exist_ok=True)
                    if os.path.exists(src_fp):
                        shutil.move(src_fp, dst_fp)
                    it["scope"] = scope
                    it["file"] = prefix_for(scope) + os.path.basename(it["file"])
                    it.pop("owner", None)
                    items = load_user_items()
                    for x in items:
                        if x["id"] == icon_id:
                            x.update(it)
                            break
                    else:
                        items.append(it)
                    save_user_items(items)
                return self._json({"ok": True, "item": it})
            except Exception as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

        return self._json({"ok": False, "error": "unknown endpoint"}, 404)

    def do_DELETE(self):
        icon_id = urlparse(self.path).path.rsplit("/", 1)[-1]
        it = index_for().get(icon_id)
        if not it or it.get("cat") != "我的图片":
            return self._json({"ok": False, "error": "只能删除自己上传的图片"}, 400)
        try:
            fp = icon_path(it)
            if os.path.exists(fp):
                os.remove(fp)
        except OSError:
            pass
        items = [x for x in load_user_items() if x["id"] != icon_id]
        save_user_items(items)
        return self._json({"ok": True})


def main():
    url = f"http://127.0.0.1:{PORT}"
    print(f"小虫光标已启动: {url}")
    print(f"图标数量: {len(CATALOG['items'])}   光标输出目录: {cursor_tools.CUR_DIR}")
    print(f"公共图片: {SHARED_DIR}")
    print(f"私人图片: {PRIVATE_DIR}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # 只绑定 127.0.0.1：局域网里的其他电脑 / 手机访问不到
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
