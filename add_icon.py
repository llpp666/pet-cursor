# -*- coding: utf-8 -*-
"""
往公共图标库里加一张图（给自己或给别人提 PR 用）

用法：
    python add_icon.py 图片.png --name 小熊猫 --cat 动物 --emoji 🐼
    python add_icon.py 图片.png --name 小猫            # 分类默认"我的图标"，emoji 可省略
    python add_icon.py 图片.png --name 小猫 --no-bump  # 只加不更新 version（批量加完再手动改）

它会做三件事：
  1. 把图片规整成 512×512 透明 PNG，存进 icons/
  2. 往 catalog.json 的 items 里加一条
  3. 把 catalog.json 的 version 改成当天日期 —— **这一步很重要**，
     别人的客户端靠 version 判断要不要重新下载，不改他们就拉不到新图标

然后你自己 commit + push 就行。
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(BASE, "icons")
CATALOG = os.path.join(BASE, "catalog.json")


def slug(name, used):
    """生成文件名：只用 ASCII —— 中文 id 进 jsDelivr 的 URL 要百分号编码，容易出问题"""
    ascii_part = "".join(ch for ch in name if ch.isascii() and ch.isalnum()).lower()
    if len(ascii_part) >= 2:
        base = ascii_part[:24]
    else:
        base = "icon" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    n, cand = 1, base
    while cand in used:
        n += 1
        cand = "%s%d" % (base, n)
    return cand


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="要加入的图片（png/jpg/webp 都行）")
    ap.add_argument("--name", required=True, help="图标名字，例如 小熊猫")
    ap.add_argument("--cat", default="我的图标", help="分类，例如 昆虫 / 动物 / 奇幻")
    ap.add_argument("--emoji", default="", help="可选的 emoji 标记")
    ap.add_argument("--no-bump", action="store_true", help="不改 version（批量添加时用）")
    args = ap.parse_args()

    if not os.path.exists(args.image):
        sys.exit("找不到图片: %s" % args.image)

    cat = json.load(open(CATALOG, encoding="utf-8"))
    items = cat.setdefault("items", [])
    used = {os.path.splitext(os.path.basename(i.get("file", "")))[0] for i in items}
    used |= {os.path.splitext(f)[0] for f in os.listdir(ICONS)}
    ident = slug(args.name, used)

    # 规整成 512×512 透明底，和内置图标保持一致
    img = Image.open(args.image)
    img.load()
    img = img.convert("RGBA")
    scale = 512 / max(img.size)
    nw, nh = max(1, round(img.size[0] * scale)), max(1, round(img.size[1] * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((512 - nw) // 2, (512 - nh) // 2))
    dst = os.path.join(ICONS, ident + ".png")
    canvas.save(dst, "PNG")

    items.append({"id": ident, "name": args.name, "emoji": args.emoji,
                  "cat": args.cat, "file": "icons/%s.png" % ident})

    today = datetime.date.today().strftime("%Y.%m.%d")
    if not args.no_bump:
        cat["version"] = today

    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=2)

    print("已加入：%s（%s）→ icons/%s.png" % (args.name, args.cat, ident))
    print("图标总数：%d" % len(items))
    if args.no_bump:
        print("注意：version 没改（%s）。批量加完后记得手动改成新日期，"
              "否则别人拉不到。" % cat.get("version", ""))
    else:
        print("version 已更新为 %s —— 别人下次启动会拉到这批新图标" % today)
    print()
    print("接下来：")
    print("  git add icons/%s.png catalog.json" % ident)
    print("  git commit -m \"新增图标：%s\"" % args.name)
    print("  git push")


if __name__ == "__main__":
    main()
