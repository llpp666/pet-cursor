# -*- coding: utf-8 -*-
"""
从 OpenMoji / Twemoji CDN 下载昆虫 & 动物图标，构建本地图标库。
- OpenMoji: CC BY-SA 4.0  (主源, 618x618 PNG)
- Twemoji : CC-BY 4.0     (备用源, 72x72 PNG)
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(BASE, "icons")
CATALOG = os.path.join(BASE, "catalog.json")

# (码位, 中文名, emoji, 分类)
ITEMS = [
    # ---- 昆虫 / 虫类 ----
    ("1F98B", "蝴蝶", "\U0001F98B", "昆虫"),
    ("1F41D", "蜜蜂", "\U0001F41D", "昆虫"),
    ("1F41C", "蚂蚁", "\U0001F41C", "昆虫"),
    ("1F41E", "瓢虫", "\U0001F41E", "昆虫"),
    ("1FAB2", "甲虫", "\U0001FAB2", "昆虫"),
    ("1F997", "蟋蟀", "\U0001F997", "昆虫"),
    ("1F99F", "蚊子", "\U0001F99F", "昆虫"),
    ("1F577", "蜘蛛", "\U0001F577", "昆虫"),
    ("1F982", "蝎子", "\U0001F982", "昆虫"),
    ("1F41B", "毛毛虫", "\U0001F41B", "昆虫"),
    ("1FAB3", "蟑螂", "\U0001FAB3", "昆虫"),
    ("1FAB1", "蚯蚓", "\U0001FAB1", "昆虫"),
    ("1F40C", "蜗牛", "\U0001F40C", "昆虫"),
    ("1F578", "蜘蛛网", "\U0001F578", "昆虫"),
    # ---- 动物 ----
    ("1F431", "猫", "\U0001F431", "动物"),
    ("1F436", "狗", "\U0001F436", "动物"),
    ("1F98A", "狐狸", "\U0001F98A", "动物"),
    ("1F43C", "熊猫", "\U0001F43C", "动物"),
    ("1F430", "兔子", "\U0001F430", "动物"),
    ("1F407", "小兔", "\U0001F407", "动物"),
    ("1F43F", "松鼠", "\U0001F43F", "动物"),
    ("1F994", "刺猬", "\U0001F994", "动物"),
    ("1F438", "青蛙", "\U0001F438", "动物"),
    ("1F422", "乌龟", "\U0001F422", "动物"),
    ("1F426", "小鸟", "\U0001F426", "动物"),
    ("1F427", "企鹅", "\U0001F427", "动物"),
    ("1F989", "猫头鹰", "\U0001F989", "动物"),
    ("1F99C", "鹦鹉", "\U0001F99C", "动物"),
    ("1F981", "狮子", "\U0001F981", "动物"),
    ("1F992", "长颈鹿", "\U0001F992", "动物"),
    ("1F428", "考拉", "\U0001F428", "动物"),
    ("1F987", "蝙蝠", "\U0001F987", "动物"),
    ("1F419", "章鱼", "\U0001F419", "动物"),
    ("1F42C", "海豚", "\U0001F42C", "动物"),
    ("1F420", "热带鱼", "\U0001F420", "动物"),
    ("1F988", "鲨鱼", "\U0001F988", "动物"),
    # ---- 奇幻 ----
    ("1F984", "独角兽", "\U0001F984", "奇幻"),
    ("1F409", "龙", "\U0001F409", "奇幻"),
    ("1F996", "霸王龙", "\U0001F996", "奇幻"),
    ("1F995", "长颈龙", "\U0001F995", "奇幻"),
]

# 注意: cdn.jsdelivr.net 在国内常被掐断 TLS，fastly 节点更稳
SOURCES = [
    ("openmoji", "https://fastly.jsdelivr.net/gh/hfg-gmuend/openmoji@master/color/618x618/{}.png"),
    ("twemoji", "https://fastly.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{}.png"),
    ("openmoji-cdn", "https://cdn.jsdelivr.net/gh/hfg-gmuend/openmoji@master/color/618x618/{}.png"),
]


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 pet-cursor-builder"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download_one(item):
    code, name, emoji, cat = item
    dst = os.path.join(ICON_DIR, code.lower() + ".png")
    if os.path.exists(dst) and os.path.getsize(dst) > 500:
        return code, "cached", dst

    tried = []
    for src_name, tpl in SOURCES:
        cp = code if src_name == "openmoji" else code.lower()
        url = tpl.format(cp)
        for attempt in range(3):  # CDN 偶发限流，重试
            try:
                data = fetch(url, timeout=30)
                if len(data) < 500:
                    tried.append(f"{src_name}:too-small")
                    break
                with open(dst, "wb") as f:
                    f.write(data)
                return code, src_name, dst
            except Exception as e:
                tried.append(f"{src_name}:{type(e).__name__}")
                time.sleep(1.2 * (attempt + 1))
    return code, "FAIL(" + ",".join(tried) + ")", None


def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for code, status, path in ex.map(download_one, ITEMS):
            results.append((code, status))
            print(f"{code}: {status}")

    ok = [c for c, s in results if s in ("openmoji", "twemoji", "cached")]
    catalog = {
        "license": "OpenMoji (CC BY-SA 4.0) / Twemoji (CC-BY 4.0)",
        "items": [
            {"id": code.lower(), "name": name, "emoji": emoji, "cat": cat,
             "file": f"icons/{code.lower()}.png"}
            for code, name, emoji, cat in ITEMS
            if code in ok
        ],
    }
    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    failed = [c for c, s in results if c not in ok]
    print(f"\n完成: {len(ok)}/{len(ITEMS)}  失败: {failed}")


if __name__ == "__main__":
    main()
