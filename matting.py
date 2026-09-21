# -*- coding: utf-8 -*-
"""
AI 抠图：
  源码模式优先使用 rembg；发布包内置 ONNX Runtime + 轻量 U²-Net 推理
  发布包直接携带约 5MB 模型；缺失时才下载到 %LOCALAPPDATA%/PetCursor/models/
  装不上 / 下载失败时回退到「四角取色 + 容差扩散」的简易去背景
"""
import os
import hashlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from PIL import Image, ImageChops, ImageDraw

# ---------------------------------------------------------------- 依赖隔离
# rembg 会牵出 pymatting -> numba -> llvmlite 这条重型链（JIT 编译器），
# 打包成 exe 后加载会直接崩溃（0xC0000005）。抠图只需要 rembg 的普通去背景，
# 用不到 pymatting 的 alpha matte，所以按需把它们替换成空壳模块。
_BLOCKED = ("pymatting", "numba", "llvmlite")
_blocker_installed = False


def _stub_module(name):
    import types
    m = types.ModuleType(name)
    m.__path__ = []

    def __getattr__(attr):
        sub = _stub_module(name + "." + attr)
        import sys
        sys.modules[sub.__name__] = sub
        setattr(m, attr, sub)
        return sub

    m.__getattr__ = __getattr__
    return m


def _install_blocker():
    global _blocker_installed
    if _blocker_installed:
        return
    _blocker_installed = True
    import importlib.abc
    import importlib.machinery
    import sys

    class _Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return _stub_module(spec.name)

        def exec_module(self, module):
            pass

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in _BLOCKED:
                return importlib.machinery.ModuleSpec(fullname, _Loader())
            return None

    sys.meta_path.insert(0, _Finder())


def _import_rembg():
    """安全地导入 rembg（失败返回 None，绝不抛给上层）"""
    try:
        _install_blocker()
        import rembg
        return rembg
    except Exception:
        return None


# 效果好的优先；本机有哪个就用哪个，都没有才用最小的 u2netp（约 4.7MB，会自动下载）
_PREFERRED = ["bria-rmbg", "u2net", "isnet-general-use",
              "u2net_human_seg", "u2netp", "silueta"]

_session_cache = {}
_last = {"ai": False, "model": "", "msg": "尚未使用", "detail": ""}
_EXTERNAL_PY = [None]     # 缓存「本机哪个 Python 装了 rembg」；"" 表示没找到
_probe_started = [False]  # 是否已触发过后台预热
_probe_lock = threading.Lock()    # 探测只会进行一次，避免并发请求重复跑
_dll_dir_lock = threading.Lock()
_DLL_DIRECTORY_UNSUPPORTED = object()

_BUNDLED_MODEL_URL = (
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
)
_BUNDLED_MODEL_MD5 = "8e83ca70e441ab06c318d82300c84806"


def _local_models():
    """扫描本机已下载的模型：{模型名: 绝对路径}"""
    home = os.path.expanduser("~")
    found = {}
    p = os.path.join(home, ".u2net", "u2net.onnx")
    if os.path.exists(p) and os.path.getsize(p) > 1024 * 1024:
        found["u2net"] = p
    root = os.path.join(home, ".rembg", "models")
    if os.path.isdir(root):
        for r, _d, files in os.walk(root):
            for f in files:
                if not f.endswith(".onnx"):
                    continue
                fp = os.path.join(r, f)
                try:
                    if os.path.getsize(fp) > 1024 * 1024:
                        found.setdefault(f[:-5], fp)
                except OSError:
                    pass
    return found


def pick_model():
    """返回 (模型名, 是否还需联网下载)"""
    local = _local_models()
    for name in _PREFERRED:
        if name in local:
            return name, False
    for name in local:                 # 本机装了别的已知模型也能用
        return name, False
    return "u2netp", True              # 都没有：用最小模型，首次自动下载


def _get_session(name):
    """new_session 很慢（加载 1GB 模型要十来秒），缓存复用"""
    if name not in _session_cache:
        from rembg import new_session
        _session_cache[name] = new_session(name)
    return _session_cache[name]


def last_info():
    """最近一次抠图到底用的是什么（给界面显示真实结果）"""
    return dict(_last)


def _dbg(msg):
    r"""抠图排查用的小日志（写在 %LOCALAPPDATA%\PetCursor\matting.log）"""
    try:
        d = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        os.makedirs(os.path.join(d, "PetCursor"), exist_ok=True)
        with open(os.path.join(d, "PetCursor", "matting.log"), "a",
                  encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _probe_warm():
    """后台把探测跑掉，别让 HTTP 请求卡在探测上"""
    try:
        _external_python()
    except Exception as e:
        _dbg("warmup EXC %s: %s" % (type(e).__name__, e))


def ensure_probe_started():
    """打包版第一次用到 AI 时，后台预热一次探测（不阻塞当前请求）"""
    if _EXTERNAL_PY[0] is not None or _probe_started[0]:
        return
    _probe_started[0] = True
    threading.Thread(target=_probe_warm, daemon=True).start()


def _external_python():
    """打包成 exe 后里面没有 rembg（onnxruntime 打包后会硬崩），
    这时借用本机已装好的 Python 来跑 AI 抠图。找到过就缓存。"""
    if _EXTERNAL_PY[0] is not None:
        return _EXTERNAL_PY[0]
    with _probe_lock:                      # 并发请求只探测一次
        if _EXTERNAL_PY[0] is not None:
            return _EXTERNAL_PY[0]
        return _probe_external_python()


def _probe_external_python():
    py = ""
    tmp = tempfile.mkdtemp(prefix="petpy_")
    ok_file = os.path.join(tmp, "ok.txt")
    probe = os.path.join(tmp, "probe.py")
    log_file = os.path.join(tmp, "probe.log")
    try:
        # 探测脚本自己写日志：ShellExecute 不走 cmd，拿不到 stdout 重定向
        with open(probe, "w", encoding="utf-8") as f:
            f.write(_PROBE_PY)
        for cand in _candidate_pythons():
            if os.path.isabs(cand) and not os.path.exists(cand):
                _dbg("skip %s (not exist)" % cand)
                continue
            for f in (ok_file, log_file):
                if os.path.exists(f):
                    os.remove(f)
            ok = _run_isolated(cand, [probe, log_file, ok_file], tmp,
                               [ok_file], timeout=120)
            _dbg("try %s -> %s" % (cand, ok))
            if not ok:
                _dbg("  log: %s" % (
                    open(log_file, encoding="utf-8", errors="replace").read()
                    .strip().replace("\n", " | ")[-300:]
                    if os.path.exists(log_file) else "(无日志)"))
            if ok:
                py = cand
                break
    except Exception as e:
        _dbg("probe EXC %s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _EXTERNAL_PY[0] = py
    _dbg("picked python = %r" % py)
    return py


def _candidate_pythons():
    env = os.environ.get("LOCALAPPDATA", "")
    # 只找系统级 Python（3.13 → 3.11）；虚拟环境里的 python 从打包程序
    # 启动时容易被 exe 的 DLL 搜索路径干扰而崩溃（0xC0000005）。
    # 想加自己的 Python 路径，往下面这个列表里补绝对路径即可。
    cands = []
    if env:
        cands += [
            os.path.join(env, "Programs", "Python", "Python313", "python.exe"),
            os.path.join(env, "Programs", "Python", "Python312", "python.exe"),
            os.path.join(env, "Programs", "Python", "Python311", "python.exe"),
        ]
    cands += ["python", "python3", "py"]
    return cands


def _clean_env():
    """给外部 Python 一份干净的环境副本

    摘掉 PyInstaller 塞进来的痕迹（_MEIPASS*、PYTHONHOME/PATH 等），
    避免子解释器去加载 exe 自带的那份 python DLL。
    注意只改这份副本，**不动全局 os.environ**（多线程服务里改全局会波及别的请求）。
    """
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("_MEIPASS") or k in (
                "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE",
                "PYTHONSTARTUP", "PYTHONOPTIMIZE"):
            env.pop(k, None)
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        exe_dir = ""
    keep = []
    for p in env.get("PATH", "").split(os.pathsep):
        if not p:
            continue
        low = p.lower()
        if exe_dir and low.startswith(exe_dir.lower()):
            continue
        if "_mei" in low or low.endswith("_internal"):
            continue
        keep.append(p)
    env["PATH"] = os.pathsep.join(keep)
    return env


def _set_windows_dll_directory(path):
    """设置 Windows DLL 搜索目录，并返回原值。

    PyInstaller 会通过 SetDllDirectoryW 把 ``_internal`` 放进 DLL 搜索路径，
    这个进程级状态会被子进程继承，不能靠清理 PATH 修复。
    """
    if os.name != "nt":
        return _DLL_DIRECTORY_UNSUPPORTED
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetDllDirectoryW.argtypes = (wintypes.DWORD, wintypes.LPWSTR)
        kernel32.GetDllDirectoryW.restype = wintypes.DWORD
        kernel32.SetDllDirectoryW.argtypes = (wintypes.LPCWSTR,)
        kernel32.SetDllDirectoryW.restype = wintypes.BOOL

        size = kernel32.GetDllDirectoryW(0, None)
        previous = None
        if size:
            buffer = ctypes.create_unicode_buffer(size + 1)
            if kernel32.GetDllDirectoryW(len(buffer), buffer):
                previous = buffer.value
        if not kernel32.SetDllDirectoryW(path):
            return _DLL_DIRECTORY_UNSUPPORTED
        return previous
    except Exception as e:
        _dbg("SetDllDirectoryW EXC %s: %s" % (type(e).__name__, e))
        return _DLL_DIRECTORY_UNSUPPORTED


def _popen_external(command, **kwargs):
    """启动不继承 PyInstaller 私有 DLL 目录的外部程序。"""
    with _dll_dir_lock:
        previous = _set_windows_dll_directory(None)
        try:
            return subprocess.Popen(command, **kwargs)
        finally:
            if previous is not _DLL_DIRECTORY_UNSUPPORTED:
                _set_windows_dll_directory(previous)


def _run_isolated(py, args, workdir, wait_files, timeout=900):
    """另起一个干净的 python 进程跑脚本，等它产出 wait_files

    用 subprocess 直接启动并传入 env（不继承）：实测打包后的 onedir 程序
    这样启动外部 Python，onnxruntime / rembg 都能正常加载。
    早先"必须走 ShellExecute"的结论是误判 —— 当时真正的故障是 cmd 的
    `/c` 引号解析把命令吃掉了（ShellExecute 还照常返回成功码 42），
    排查方向被带偏。这里不再碰全局 os.environ（多线程服务里不安全）。
    """
    flags = 0x08000000 if os.name == "nt" else 0        # CREATE_NO_WINDOW
    try:
        p = _popen_external(
            [py] + list(args), env=_clean_env(), cwd=workdir,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags,
        )
    except Exception as e:
        _dbg("Popen(%s) EXC %s: %s" % (py, type(e).__name__, e))
        return False
    end = time.time() + timeout
    while time.time() < end:
        if all(os.path.exists(f) for f in wait_files):
            _kill(p)
            return True
        if p.poll() is not None:            # 进程已退出
            time.sleep(0.4)                 # 给文件写入留一点时间
            ok = all(os.path.exists(f) for f in wait_files)
            if not ok:
                _dbg("proc exit rc=%s 但没产出 %s" % (p.returncode, wait_files))
            return ok
        time.sleep(0.3)
    _kill(p)
    _dbg("超时 %ss 未产出" % timeout)
    return False


def _kill(p):
    try:
        p.kill()
    except Exception:
        pass


# 兼容旧调用名（内部已不再使用 ShellExecute，保留以免外部引用报错）
def _with_clean_env(fn):
    return fn()


# 探测脚本：在外部 Python 里跑，能 import rembg 就写 ok 文件
# 逐步 flush，这样万一中途卡住/崩溃，日志里能看出卡在哪一步
_PROBE_PY = (
    "import sys, traceback\n"
    "log, ok = sys.argv[1], sys.argv[2]\n"
    "lf = open(log, 'w', encoding='utf-8', buffering=1)\n"
    "def w(m):\n"
    "    lf.write(str(m) + '\\n'); lf.flush()\n"
    "w('start py=' + sys.version.split()[0])\n"
    "try:\n"
    "    import numpy; w('numpy ' + numpy.__version__)\n"
    "    import onnxruntime; w('ort ' + onnxruntime.__version__)\n"
    "    import rembg; w('rembg ' + str(getattr(rembg, '__version__', '?')))\n"
    "    open(ok, 'w').write('ok')\n"
    "    w('DONE')\n"
    "except Exception:\n"
    "    w(traceback.format_exc())\n"
)

# 外部 Python 里跑的抠图脚本（写成文件，避免命令行的引号地狱）
_WORKER_PY = (
    "import sys, traceback\n"
    "src, dst, model, done, log = sys.argv[1:6]\n"
    "lf = open(log, 'w', encoding='utf-8', buffering=1)\n"
    "sys.stdout = sys.stderr = lf\n"
    "try:\n"
    "    from PIL import Image\n"
    "    from rembg import remove, new_session\n"
    "    lf.write('loading model %s\\n' % model); lf.flush()\n"
    "    img = Image.open(src).convert('RGBA')\n"
    "    sess = new_session(model) if model else None\n"
    "    out = remove(img, session=sess) if sess else remove(img)\n"
    "    if isinstance(out, bytes):\n"
    "        import io; out = Image.open(io.BytesIO(out))\n"
    "    out.convert('RGBA').save(dst)\n"
    "    lf.write('saved\\n'); lf.flush()\n"
    "    open(done, 'w').write('done')\n"
    "except Exception:\n"
    "    lf.write(traceback.format_exc())\n"
    "finally:\n"
    "    lf.flush()\n"
)


def _external_remove_bg(img, model):
    """用外部 Python 抠图；成功返回 RGBA 图，失败返回 None"""
    py = _external_python()
    if not py:
        return None
    tmp = tempfile.mkdtemp(prefix="petmat_")
    src, dst = os.path.join(tmp, "in.png"), os.path.join(tmp, "out.png")
    done = os.path.join(tmp, "done.txt")
    worker = os.path.join(tmp, "worker.py")
    log = os.path.join(tmp, "worker.log")
    img.convert("RGBA").save(src)
    try:
        with open(worker, "w", encoding="utf-8") as f:
            f.write(_WORKER_PY)
        args = [worker, src, dst, model, done, log]
        if not _run_isolated(py, args, tmp, [done]):
            tail = ""
            if os.path.exists(log):
                tail = (open(log, encoding="utf-8", errors="replace").read()
                        .strip().replace("\n", " | ")[-200:])
            _last["detail"] = "AI 抠图没跑完" + ("：" + tail if tail else "（超时）")
            _dbg("worker failed: %s" % _last["detail"])
            return None
        return Image.open(dst).convert("RGBA")
    except Exception as e:
        _last["detail"] = "%s: %s" % (type(e).__name__, e)
        return None
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


def _bundled_engine_available():
    """发布包是否带有轻量 AI 推理依赖。"""
    return (importlib.util.find_spec("numpy") is not None
            and importlib.util.find_spec("onnxruntime") is not None)


def _bundled_model_path(download=False):
    """查找轻量 U²-Net 模型；需要时下载并校验后原子落盘。"""
    home = os.path.expanduser("~")
    app_data = os.environ.get("LOCALAPPDATA") or home
    target = os.path.join(app_data, "PetCursor", "models", "u2netp.onnx")
    candidates = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(os.path.join(bundle_root, "models", "u2netp.onnx"))
    candidates += [
        target,
        os.path.join(home, ".rembg", "models", "u2netp", "u2netp.onnx"),
        os.path.join(home, ".u2net", "u2netp.onnx"),
    ]
    for candidate in candidates:
        try:
            if os.path.getsize(candidate) > 1024 * 1024:
                return candidate
        except OSError:
            pass
    if not download:
        return None

    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary = target + ".download"
    try:
        digest = hashlib.md5()
        with urllib.request.urlopen(_BUNDLED_MODEL_URL, timeout=60) as response:
            with open(temporary, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != _BUNDLED_MODEL_MD5:
            raise RuntimeError("AI 模型校验失败，请检查网络后重试")
        os.replace(temporary, target)
        return target
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


def _preload_system_runtime():
    """子进程里优先加载系统的 VC 运行库，绕开包内那份版本不合的。

    系统缺这些 DLL 时静默跳过，继续用包内自带的。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
    except Exception:
        return
    root = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
                 "msvcp140_2.dll", "concrt140.dll", "vcomp140.dll"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            try:
                ctypes.WinDLL(p)
            except OSError:
                pass


def _bundled_infer_into(src, dst, model_path):
    """进程内推理：读 src 图，跑 U²-Net，写 dst（RGBA PNG）。失败抛异常。"""
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    rgba = Image.open(src).convert("RGBA")
    session = ort.InferenceSession(
        model_path, providers=["CPUExecutionProvider"]
    )

    resized = rgba.convert("RGB").resize((320, 320), Image.Resampling.LANCZOS)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    mean = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)
    std = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)
    tensor = ((array - mean) / std).transpose((2, 0, 1))[None]
    prediction = session.run(
        None, {session.get_inputs()[0].name: tensor.astype(np.float32)}
    )[0][:, 0, :, :]
    minimum = float(np.min(prediction))
    maximum = float(np.max(prediction))
    if maximum <= minimum:
        raise RuntimeError("AI 模型返回了无效遮罩")
    prediction = np.squeeze((prediction - minimum) / (maximum - minimum))
    mask = Image.fromarray(
        (np.clip(prediction, 0, 1) * 255).astype("uint8"), mode="L"
    ).resize(rgba.size, Image.Resampling.LANCZOS)
    mask = ImageChops.multiply(mask, rgba.getchannel("A"))
    result = rgba.copy()
    result.putalpha(mask)
    result.save(dst, "PNG")


def _read_tail(path, limit=300):
    try:
        return (open(path, encoding="utf-8", errors="replace").read()
                .strip().replace("\n", " | ")[-limit:])
    except OSError:
        return ""


def _bundled_worker(args, wait_files, timeout):
    """起一个 exe 自身的 --matting-worker 子进程，等它产出 wait_files。

    返回 (是否成功, 日志尾巴)。子进程崩溃只丢这一单 AI，主服务不受影响。
    """
    if not getattr(sys, "frozen", False):
        return False, "worker 仅在打包版可用"
    flags = 0x08000000 if os.name == "nt" else 0        # CREATE_NO_WINDOW
    log_file = args[-1]
    try:
        p = _popen_external(
            [sys.executable, "--matting-worker"] + list(args),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags,
        )
    except Exception as e:
        _dbg("worker Popen EXC %s: %s" % (type(e).__name__, e))
        return False, "%s: %s" % (type(e).__name__, e)
    end = time.time() + timeout
    while time.time() < end:
        if all(os.path.exists(f) for f in wait_files):
            _kill(p)
            return True, ""
        if p.poll() is not None:
            time.sleep(0.4)
            if all(os.path.exists(f) for f in wait_files):
                return True, ""
            return False, _read_tail(log_file) or "worker 提前退出（rc=%s）" % p.returncode
        time.sleep(0.3)
    _kill(p)
    return False, "超时 %ss 未产出" % timeout


# 内置引擎的体检结果：unknown=还没测 / ok / fail
_BUNDLED_PROBE = {"started": False, "state": "unknown", "detail": ""}
_bundled_probe_lock = threading.Lock()


def _bundled_probe_worker():
    tmp = tempfile.mkdtemp(prefix="petbw_")
    ok = os.path.join(tmp, "ok.txt")
    log = os.path.join(tmp, "probe.log")
    try:
        model = _bundled_model_path(download=False)
        args = ["probe", ok, log]
        if model:
            args.append(model)          # 有模型就顺带把推理会话建起来
        good, tail = _bundled_worker(args, [ok], 150)
        _BUNDLED_PROBE["state"] = "ok" if good else "fail"
        _BUNDLED_PROBE["detail"] = "" if good else (
            tail or "worker 未产出结果（可能已崩溃）")
        _dbg("bundled probe -> %s %s" % (
            _BUNDLED_PROBE["state"], _BUNDLED_PROBE["detail"][:200]))
    except Exception as e:
        _BUNDLED_PROBE["state"] = "fail"
        _BUNDLED_PROBE["detail"] = "%s: %s" % (type(e).__name__, e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_bundled_probe():
    """后台体检一次内置引擎（import onnxruntime + 建推理会话）。

    必须在子进程里测：万一 onnxruntime 在主进程里加载就硬崩，
    服务会当场死掉 —— 那正是要修的 bug。
    """
    if _BUNDLED_PROBE["started"]:
        return
    with _bundled_probe_lock:
        if _BUNDLED_PROBE["started"]:
            return
        _BUNDLED_PROBE["started"] = True
        threading.Thread(target=_bundled_probe_worker, daemon=True).start()


def _bundled_remove_bg(img):
    """发布包内置引擎。打包版把推理放进子进程：
    就算 onnxruntime 把子进程搞崩，主服务也活着，用户只会看到
    「AI 抠图失败，已改用简易去背景」而不是整个软件失联。"""
    model = _bundled_model_path(download=True)      # 失败会抛，由上层接住
    if getattr(sys, "frozen", False):
        tmp = tempfile.mkdtemp(prefix="petbi_")
        src = os.path.join(tmp, "in.png")
        dst = os.path.join(tmp, "out.png")
        done = os.path.join(tmp, "done.txt")
        log = os.path.join(tmp, "worker.log")
        try:
            img.convert("RGBA").save(src)
            good, tail = _bundled_worker(
                ["run", src, dst, model, done, log], [done], 120)
            if not good:
                raise RuntimeError("内置引擎推理失败"
                                   + (("：" + tail) if tail else "（子进程未响应）"))
            return Image.open(dst).convert("RGBA")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    # 源码模式（极少走到这里）：直接进程内推理
    tmp = tempfile.mkdtemp(prefix="petbi_")
    src = os.path.join(tmp, "in.png")
    dst = os.path.join(tmp, "out.png")
    try:
        img.convert("RGBA").save(src)
        _bundled_infer_into(src, dst, model)
        return Image.open(dst).convert("RGBA")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def worker_main(argv):
    """exe 以 --matting-worker 启动时进入这里。

    在这个独立进程里 import onnxruntime / 跑推理 —— 它崩了也只是
    这个子进程死掉，主服务进程毫发无损。
    argv: [--matting-worker, probe, ok_file, log_file[, model]]
          [--matting-worker, run, src, dst, model, done, log]
    """
    _preload_system_runtime()
    i = list(argv).index("--matting-worker")
    args = list(argv)[i + 1:]
    mode = args[0] if args else ""
    if mode == "probe":
        _m, ok_file, log_file = args[0], args[1], args[2]
        model = args[3] if len(args) > 3 else None
        lf = open(log_file, "w", encoding="utf-8", buffering=1)
        try:
            import numpy
            lf.write("numpy %s\n" % numpy.__version__)
            import onnxruntime as ort
            lf.write("ort %s\n" % ort.__version__)
            if model:
                ort.InferenceSession(model, providers=["CPUExecutionProvider"])
                lf.write("session ok\n")
            lf.write("DONE\n")
            open(ok_file, "w").write("ok")
        except Exception:
            import traceback
            lf.write(traceback.format_exc())
        finally:
            lf.flush()
        return 0
    if mode == "run":
        _m, src, dst, model, done, log_file = args
        lf = open(log_file, "w", encoding="utf-8", buffering=1)
        try:
            _bundled_infer_into(src, dst, model)
            lf.write("saved\nDONE\n")
            open(done, "w").write("done")
        except Exception:
            import traceback
            lf.write(traceback.format_exc())
        finally:
            lf.flush()
        return 0
    return 2


def engine_status():
    """返回 (是否可用 AI, 模型是否已下载, 说明)

    绝不阻塞：检测本机 Python 是耗时的，放到后台线程；请求只读结果。
    """
    rembg = _import_rembg()
    name, need_dl = pick_model()
    if rembg is not None:
        if need_dl:
            return True, False, "首次使用需下载模型（约 5MB），请稍候"
        return True, True, "AI 引擎已就绪（%s）" % name
    if _bundled_engine_available():
        # 文件在 ≠ 引擎能用：onnxruntime 是原生 DLL，缺系统运行库时加载就崩。
        # 真正的体检放到子进程里做，结果只在这里被读取，绝不阻塞。
        ensure_bundled_probe()
        model_ready = _bundled_model_path() is not None
        state = _BUNDLED_PROBE["state"]
        if state == "unknown":
            return True, model_ready, "正在检测内置 AI 引擎…（首次约几秒）"
        if state == "fail":
            return False, model_ready, (
                "内置 AI 引擎启动不了（%s），已自动改用简易去背景"
                % (_BUNDLED_PROBE["detail"][:90] or "未知原因"))
        if model_ready:
            return True, True, "AI 引擎已就绪（内置 U²-Net）"
        return True, False, "首次使用需下载模型（约 5MB），请稍候"
    # 打包版：靠本机 Python 跑 AI
    ensure_probe_started()
    py = _EXTERNAL_PY[0]
    if py is None:
        return True, False, "正在检测本机 AI 引擎…（首次约需几秒）"
    if not py:
        return False, False, "本机没有可用的 AI 引擎，将使用简易去背景"
    if need_dl:
        return True, False, "首次使用需下载模型（约 5MB），请稍候"
    return True, True, "AI 引擎已就绪（本机 Python）"


def remove_bg(img):
    """输入 PIL 图 -> 去背景后的 RGBA 图

    用哪种方式抠的记在 last_info() 里，供界面如实告诉用户，
    避免"点了 AI 抠图、其实悄悄退化成简易去背景"这种假成功。
    """
    img = img.convert("RGBA")
    rembg = _import_rembg()
    if rembg is not None:
        try:
            from rembg import remove
            name, need_dl = pick_model()
            sess = _get_session(name)
            out = remove(img, session=sess)
            if isinstance(out, bytes):
                import io
                out = Image.open(io.BytesIO(out))
            _last.update({"ai": True, "model": name,
                          "msg": "AI 抠图完成（%s）" % name})
            return out.convert("RGBA")
        except Exception as e:
            _last.update({"ai": False, "model": "", "detail": "",
                          "msg": "AI 抠图失败：%s: %s，已改用简易去背景"
                                 % (type(e).__name__, e)})
    else:
        # 发布包优先走内置模型，避免本机大模型耗尽内存或受外部 DLL 干扰。
        if _bundled_engine_available():
            try:
                out = _bundled_remove_bg(img)
                _last.update({"ai": True, "model": "u2netp（内置）",
                              "detail": "", "msg": "AI 抠图完成（内置 U²-Net）"})
                return out
            except Exception as e:
                _last.update({"ai": False, "model": "",
                              "detail": "%s: %s" % (type(e).__name__, e),
                              "msg": "AI 抠图失败：%s: %s，已改用简易去背景"
                                     % (type(e).__name__, e)})
        name, need_dl = pick_model()
        if not need_dl:
            out = _external_remove_bg(img, name)
            if out is not None:
                _last.update({"ai": True, "model": name + "（本机 Python）",
                              "detail": "", "msg": "AI 抠图完成（%s）" % name})
                return out
        if need_dl:
            _last.update({"ai": False, "model": "",
                          "msg": "本机没有 AI 模型，已改用简易去背景"})
        else:
            _last.update({"ai": False, "model": "",
                          "msg": "没能用上 AI：" + (_last.get("detail")
                                                 or "本机没找到装了 AI 引擎的 Python")
                                 + "，已改用简易去背景"})
    return _simple_remove_bg(img)


def _simple_remove_bg(img, tol=48):
    """四角取样 + 容差扩散去背景（对纯色 / 简单背景有效）"""
    img = img.convert("RGBA")
    w, h = img.size
    rgb = img.convert("RGB")
    marker = (255, 0, 255)
    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        try:
            ImageDraw.floodfill(rgb, corner, marker, thresh=tol)
        except Exception:
            pass

    alpha = Image.new("L", (w, h), 255)
    px = rgb.load()
    ap = alpha.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r > 240 and g < 20 and b > 240:
                ap[x, y] = 0
    img.putalpha(alpha)
    return img


def trim_transparent(img, pad=2, alpha_threshold=16):
    """裁掉四周全透明的边，让主体占满画面"""
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    if alpha_threshold > 0:
        alpha = alpha.point(lambda value: 255 if value > alpha_threshold else 0)
    bbox = alpha.getbbox()
    if not bbox:
        return img
    w, h = img.size
    x0 = max(0, bbox[0] - pad)
    y0 = max(0, bbox[1] - pad)
    x1 = min(w, bbox[2] + pad)
    y1 = min(h, bbox[3] + pad)
    return img.crop((x0, y0, x1, y1))


def to_square(img, size=512, pad_ratio=0.02):
    """贴回正方形画布（保留主体比例，四周留一点边距）"""
    img = trim_transparent(img)
    w, h = img.size
    s = max(w, h)
    s = int(s * (1 + pad_ratio * 2))
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((s - w) // 2, (s - h) // 2))
    return canvas.resize((size, size), Image.LANCZOS)
