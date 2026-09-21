# -*- coding: utf-8 -*-
"""
小蝴蝶编号 —— "你是第 X 位小蝴蝶"

零服务器、零资质的做法：编号记在**软件文件夹**里，跟着软件一起走。
  · 同一台电脑：第一次运行领一个号，之后永远显示这一个号（写在账户目录，不受软件重装影响）
  · 换一台电脑（你把软件拷给别人、别人下载一份）：那台电脑第一次运行时编号 +1
所以编号 = 这份软件一共在多少台电脑上跑起来过，也就近似"有多少人下载了"。

全程离线，不联网、不回传任何数据。
"""
import json
import os
import sys
import time
import uuid

APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "PetCursor"
)
INSTALL_FILE = os.path.join(APP_DIR, "install.json")
COUNTER_NAME = "小蝴蝶编号.json"

_counter_dir = None


def set_counter_dir(d):
    """设置计数器存放目录（软件所在目录，随软件传播）"""
    global _counter_dir
    _counter_dir = d


def _default_counter_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    env = os.environ.get("PETCURSOR_USER_DIR")
    if env:
        return os.path.dirname(env)
    return os.path.dirname(os.path.abspath(__file__))


def counter_file():
    return os.path.join(_counter_dir or _default_counter_dir(), COUNTER_NAME)


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _machine_id():
    """一个稳定的本机标识（只用于避免重复领号，不含任何个人信息）"""
    inst = _load(INSTALL_FILE)
    if inst.get("machine"):
        return inst["machine"]
    return uuid.uuid4().hex[:12]


def claim():
    """领取（或读取）本机编号

    返回 dict: {seq, total, new, first_at, counter_file}
    """
    inst = _load(INSTALL_FILE)
    cfile = counter_file()
    counter = _load(cfile)
    total = int(counter.get("last", 0) or 0)

    if inst.get("seq"):
        seq = int(inst["seq"])
        # 本机已领过号；如果软件文件夹的计数器被换成了更小的（重新打包等），补回去
        if seq > total:
            counter["last"] = seq
            _save(cfile, counter)
            total = seq
        return {"seq": seq, "total": total, "new": False,
                "first_at": inst.get("first_at", ""), "counter_file": cfile}

    seq = total + 1
    mid = _machine_id()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    counter["last"] = seq
    counter.setdefault("log", []).append({"seq": seq, "at": now, "machine": mid})
    counter["log"] = counter["log"][-500:]      # 只留最近 500 条，别让文件无限涨
    _save(cfile, counter)

    inst = {"seq": seq, "machine": mid, "first_at": now}
    _save(INSTALL_FILE, inst)
    return {"seq": seq, "total": seq, "new": True,
            "first_at": now, "counter_file": cfile}


def info():
    try:
        return claim()
    except Exception:
        return {"seq": 0, "total": 0, "new": False, "first_at": "",
                "counter_file": counter_file()}


if __name__ == "__main__":
    print(json.dumps(info(), ensure_ascii=False, indent=2))
