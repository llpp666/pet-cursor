# -*- coding: utf-8 -*-
"""
社区公共图标库：从 GitHub 仓库（经 jsDelivr 分发）增量拉取图标

设计要点（开源、无自建服务器）：
  · 公共图标库就是仓库里的 catalog.json + icons/，谁都能提 PR 加图标
  · 客户端只**下载**，从不上传 —— 用户自己的图永远留在本机
  · 拉不到网就静默降级到软件内置的那份图标，离线可用
  · 用 jsDelivr 的 fastly 节点（cdn 节点在国内经常被掐，实测不可用）

仓库地址写在 community.json（不进 git，仓库里只放 community.example.json）：
    {"repo": "你的用户名/仓库名", "branch": "main"}
repo 为空 = 关闭社区同步，只用内置图标库。
"""
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# jsDelivr 有多个节点，本机实测 cdn. 节点 SSL 会被中途掐断，只有 fastly. 稳定
CDN = "https://fastly.jsdelivr.net/gh"
UA = "PetCursor/1.0 (+local desktop app)"

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "community.json")
CONFIG_EXAMPLE = os.path.join(BASE, "community.example.json")

CACHE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "PetCursor", "community")
CACHE_CATALOG = os.path.join(CACHE_DIR, "catalog.json")
CACHE_ICONS = os.path.join(CACHE_DIR, "icons")

_state = {
    "enabled": False,
    "repo": "",
    "branch": "main",
    "remote_version": "",
    "local_version": "",
    "remote_count": 0,
    "synced_count": 0,
    "last_check": 0,
    "last_error": "",
    "checking": False,
}
_lock = threading.Lock()


# ---------------------------------------------------------------- 配置
def load_config():
    """读 community.json；没有就退回 example（repo 为空即关闭）"""
    path = CONFIG_FILE if os.path.exists(CONFIG_FILE) else CONFIG_EXAMPLE
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    repo = str(cfg.get("repo", "") or "").strip().strip("/")
    branch = str(cfg.get("branch", "main") or "main").strip() or "main"
    return repo, branch


def status():
    with _lock:
        repo, branch = _state["repo"], _state["branch"]
        return {
            "ok": True,
            "enabled": _state["enabled"],
            "repo": repo,
            "branch": branch,
            "remote_version": _state["remote_version"],
            "local_version": _state["local_version"],
            "remote_count": _state["remote_count"],
            "synced_count": _state["synced_count"],
            "last_error": _state["last_error"],
            "checking": _state["checking"],
            "cache_dir": CACHE_DIR if _state["enabled"] else "",
        }


def _url(repo, branch, path):
    return "%s/%s@%s/%s" % (CDN, repo, branch, path.lstrip("/"))


def _fetch(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- 同步
def sync(force=False):
    """拉一次远端清单，把缺的图标下载到缓存目录

    静默失败：任何网络/解析问题都只记 last_error，不影响软件本体。
    返回 (ok, 说明)
    """
    with _lock:
        if _state["checking"]:
            return False, "正在同步中"
        _state["checking"] = True
    try:
        return _sync_inner(force)
    except Exception as e:
        with _lock:
            _state["last_error"] = "%s: %s" % (type(e).__name__, e)
            _state["last_check"] = time.time()
        return False, str(e)
    finally:
        with _lock:
            _state["checking"] = False


def _sync_inner(force):
    repo, branch = load_config()
    with _lock:
        _state["repo"], _state["branch"] = repo, branch
        _state["enabled"] = bool(repo)
        _state["last_check"] = time.time()
    if not repo:
        return False, "未配置社区仓库（community.json 里填 repo）"

    raw = _fetch(_url(repo, branch, "catalog.json"))
    remote = json.loads(raw.decode("utf-8"))
    if not isinstance(remote, dict) or not isinstance(remote.get("items"), list):
        raise ValueError("远端 catalog.json 格式不对")

    r_items = remote["items"]
    r_ver = str(remote.get("version", "") or "")
    with _lock:
        _state["remote_version"] = r_ver
        _state["remote_count"] = len(r_items)

    # 版本没变且缓存已存在 → 不必重复下载
    cached = _load_cached()
    if (not force and cached
            and r_ver and cached.get("version") == r_ver
            and os.path.isdir(CACHE_ICONS)):
        with _lock:
            _state["local_version"] = r_ver
            _state["synced_count"] = len(cached.get("items", []))
            _state["last_error"] = ""
        return True, "已是最新（v%s，%d 个图标）" % (r_ver, len(r_items))

    os.makedirs(CACHE_ICONS, exist_ok=True)

    jobs = []
    for it in r_items:
        f = it.get("file", "")
        if not f:
            continue
        name = os.path.basename(f)
        dst = os.path.join(CACHE_ICONS, name)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            continue
        jobs.append((name, dst))

    def _dl(job):
        """并发下载单个图标；失败重试一次。返回 (name, ok, 错误说明)"""
        name, dst = job
        url = _url(repo, branch, "icons/" + name)
        tmp = dst + ".part"
        last = ""
        for _ in range(2):
            try:
                data = _fetch(url, timeout=15)
                if not data:
                    raise ValueError("空响应")
                with open(tmp, "wb") as fp:
                    fp.write(data)
                os.replace(tmp, dst)
                return name, True, ""
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
        return name, False, last

    if jobs:
        # 串行下载在国内网络下实测要 8 分钟，并发后可降到 1 分钟内
        with ThreadPoolExecutor(max_workers=6) as ex:
            for name, ok, err in ex.map(_dl, jobs):
                if not ok:
                    with _lock:
                        _state["last_error"] = "%s 下载失败: %s" % (name, err)

    got = []
    for it in r_items:
        f = it.get("file", "")
        if not f:
            continue
        name = os.path.basename(f)
        dst = os.path.join(CACHE_ICONS, name)
        if not (os.path.exists(dst) and os.path.getsize(dst) > 0):
            continue
        item = dict(it)
        item["file"] = "community/" + name      # 给前端用的相对路径
        item["_abs"] = dst                      # 内部真实路径，不返回给前端
        got.append(item)

    if not got:
        raise RuntimeError("一个图标都没拉下来")

    with open(CACHE_CATALOG, "w", encoding="utf-8") as f:
        json.dump({"version": r_ver, "items": [
            {k: v for k, v in it.items() if k != "_abs"} for it in got]},
            f, ensure_ascii=False, indent=2)

    with _lock:
        _state["local_version"] = r_ver
        _state["synced_count"] = len(got)
        _state["last_error"] = ""
    return True, "同步完成（v%s，%d 个图标）" % (r_ver or "?", len(got))


def _load_cached():
    if not os.path.exists(CACHE_CATALOG):
        return None
    try:
        with open(CACHE_CATALOG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def community_items():
    """给软件用的社区图标列表（带真实路径 _abs）；没配仓库或没同步过就返回空"""
    repo, _branch = load_config()
    if not repo:                      # 关掉功能时，连缓存里的也不再展示
        return []
    cached = _load_cached()
    if not cached:
        return []
    out = []
    for it in cached.get("items", []):
        name = os.path.basename(it.get("file", ""))
        p = os.path.join(CACHE_ICONS, name)
        if os.path.exists(p):
            item = dict(it)
            item["_abs"] = p
            item["cat"] = it.get("cat") or "社区"
            out.append(item)
    return out


def start_background_check(delay=2.0):
    """软件启动时后台检查一次，绝不让用户等"""
    def run():
        time.sleep(delay)
        try:
            sync()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()
