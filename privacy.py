# -*- coding: utf-8 -*-
"""
私人区口令锁

为什么需要：软件是纯本地的，「私人区」的边界其实只是**这台电脑的这个 Windows 账户** ——
能坐到你电脑前登你账户的人，一样能看到私人图片。加了口令锁才是真的「只有我知道」。

实现要点（无服务器，全在本机）：
  · 口令只存 **PBKDF2-HMAC-SHA256 派生值 + 随机盐**，明文口令从不落盘
  · 校验用 hmac.compare_digest，防时序侧信道
  · 解锁状态只在**进程内存**里，软件关掉重开就要重新输
  · 忘了口令可以删掉 lock 文件重设，但**那样也读不出原图**——没有后门，
    这是本地加密的常识，别指望「找回密码」
  · 锁住时私人图片不进列表、不能预览、不能应用、不能上传进去
"""
import hashlib
import hmac
import json
import os
import secrets
import threading

BASE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PetCursor")
LOCK_FILE = os.path.join(BASE_DIR, "private_lock.json")

ITERATIONS = 200_000
_lock = threading.Lock()
_unlocked = False          # 只在内存里：进程退出即失效


# ---------------------------------------------------------------- 存储
def _load():
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    tmp = LOCK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LOCK_FILE)


def _derive(pwd, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac(
        "sha256", pwd.encode("utf-8"), salt, int(iterations)).hex()


# ---------------------------------------------------------------- 状态
def status():
    d = _load()
    enabled = bool(d.get("enabled")) and bool(d.get("salt")) and bool(d.get("hash"))
    return {"enabled": enabled, "locked": enabled and not _unlocked}


def is_locked():
    """私人区当前是否处于锁定状态（没设口令 = 不锁）"""
    return status()["locked"]


def verify(pwd):
    d = _load()
    if not (d.get("enabled") and d.get("salt") and d.get("hash")):
        return False
    calc = _derive(pwd or "", bytes.fromhex(d["salt"]), d.get("iterations", ITERATIONS))
    return hmac.compare_digest(calc, d["hash"])


# ---------------------------------------------------------------- 操作
def set_password(old, new, force=False):
    """首次设置 / 修改口令。返回 (ok, 说明)

    force=True 用于「忘了口令」的强制重设：跳过原口令校验。
    注意这只是**隐藏列表**的锁，不是文件加密 —— 重设后原来的私人图片会全部重新显示出来。
    """
    global _unlocked
    new = (new or "").strip()
    if len(new) < 4:
        return False, "口令至少 4 位"
    with _lock:
        d = _load()
        if not force and d.get("enabled") and d.get("hash"):
            if not old or not verify(old):
                return False, "原口令不对"
        salt = secrets.token_bytes(16)
        _save({
            "enabled": True,
            "salt": salt.hex(),
            "hash": _derive(new, salt),
            "iterations": ITERATIONS,
            "hint": (d.get("hint") or ""),
        })
        _unlocked = True          # 设完直接进入解锁态，省得再输一次
    return True, "口令已设置"


def clear_password(old):
    """关闭口令锁（私人区恢复成不设防）。返回 (ok, 说明)"""
    global _unlocked
    with _lock:
        d = _load()
        if not (d.get("enabled") and d.get("hash")):
            return True, "本来就没设口令"
        if not verify(old or ""):
            return False, "口令不对"
        _save({"enabled": False})
        _unlocked = False
    return True, "口令锁已关闭"


def unlock(pwd):
    global _unlocked
    with _lock:
        if not verify(pwd or ""):
            return False, "口令不对"
        _unlocked = True
    return True, "已解锁"


def lock():
    global _unlocked
    with _lock:
        _unlocked = False
    return True, "已锁定"
