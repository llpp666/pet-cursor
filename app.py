# -*- coding: utf-8 -*-
"""
小虫光标 · Pet Cursor —— 桌面应用外壳
启动本地服务，并用独立窗口打开界面（无浏览器地址栏，像原生软件一样）。
打包成 exe 后：内置资源在 sys._MEIPASS，用户上传图片保存在 exe 同级 user_icons 目录。
"""
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

APP_NAME = "小虫光标"
PORT = int(os.environ.get("PETCURSOR_PORT", "8899"))
URL = f"http://127.0.0.1:{PORT}"
FROZEN = getattr(sys, "frozen", False)


def base_dir():
    """内置资源目录（打包后为临时解压目录）"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def exe_dir():
    """用户数据目录（exe 同级，可写）"""
    if FROZEN:
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def port_alive(port=PORT, timeout=0.4):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wait_ready(seconds=25):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(URL + "/api/status", timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def start_server():
    """在后台线程里启动本地服务；返回 httpd（已占用端口时返回 None）"""
    if port_alive():
        return None
    sys.path.insert(0, base_dir())
    os.environ.setdefault(
        "PETCURSOR_USER_DIR", os.path.join(exe_dir(), "user_icons")
    )
    import server  # noqa: E402
    from http.server import ThreadingHTTPServer  # noqa: E402

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


CANDIDATES = [
    ("Google Chrome", [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]),
    ("Microsoft Edge", [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]),
]


def find_browser():
    for _name, paths in CANDIDATES:
        for p in paths:
            if os.path.exists(p):
                return p
    for name in ("chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def fresh_profile():
    """每次启动用一个独立配置目录

    固定目录时，若已有旧窗口在运行，Chrome 会把网址交给旧窗口后立刻退出，
    本程序随即关闭本地服务 —— 旧窗口还开着，图片就全部加载失败（裂图）。
    """
    root = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "PetCursor", "AppWindow",
    )
    os.makedirs(root, exist_ok=True)
    prof = os.path.join(root, "p" + time.strftime("%Y%m%d%H%M%S"))
    os.makedirs(prof, exist_ok=True)
    try:  # 只保留最近 3 份，避免配置目录越攒越大
        olds = sorted((d for d in os.listdir(root) if d.startswith("p")),
                      reverse=True)[3:]
        for d in olds:
            shutil.rmtree(os.path.join(root, d), ignore_errors=True)
    except Exception:
        pass
    return prof


def open_native_window(url):
    """优先 pywebview 原生窗口；否则用 Chrome/Edge 的 app 模式（无地址栏）"""
    if not FROZEN:
        try:
            import importlib
            webview = importlib.import_module("webview")
            win = webview.create_window(
                APP_NAME, url, width=1180, height=820, min_size=(900, 620)
            )
            webview.start()
            return win is not None
        except Exception:
            pass
    browser = find_browser()
    if not browser:
        import webbrowser
        webbrowser.open(url)
        return True
    profile = fresh_profile()
    cmd = [
        browser,
        f"--app={url}",
        "--window-size=1180,820",
        f"--user-data-dir={profile}",
        "--disable-features=Translate,Extensions",
        "--no-first-run",
        "--no-default-browser-check",
        f"--app-title={APP_NAME}",
    ]
    try:
        proc = subprocess.Popen(cmd)
        proc.wait()
        return True
    except Exception:
        import webbrowser
        webbrowser.open(url)
        return True


def main():
    args = [a.lower() for a in sys.argv[1:]]
    raw_args = sys.argv[1:]

    # AI 抠图 worker：由主服务以独立子进程方式拉起，专跑 onnxruntime 推理。
    # 它崩了只丢这一单 AI，主服务（主进程）不受影响 —— 对应 matting.worker_main。
    if "--matting-worker" in raw_args:
        sys.path.insert(0, base_dir())
        import matting  # noqa: E402
        return matting.worker_main(sys.argv)

    # 纯守护模式：不启服务、不开窗口，只在后台守住光标（开机自启用）
    if "--guard-only" in args:
        sys.path.insert(0, base_dir())
        import cursor_tools  # noqa: E402
        st = cursor_tools.guard_state()
        if not st.get("on") or not st.get("map"):
            return 0
        cursor_tools.guard_thread_start()
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        return 0

    httpd = start_server()
    if not wait_ready():
        sys.stderr.write("服务启动失败\n")
        return 1
    if "--server-only" in args:
        sys.stdout.write(f"{APP_NAME} 服务已启动: {URL}\n")
        sys.stdout.flush()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0
    open_native_window(URL)
    if httpd:
        try:
            httpd.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
