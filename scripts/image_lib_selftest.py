# -*- coding: utf-8 -*-
"""随机图图库自测（不需要微信、不联网）：扫描 / 随机挑 / 尽量不重复 / 冷却 / 空库与超限文案 / 在线图源未配置。
用法：py -3 scripts/image_lib_selftest.py
"""
import os
import sys

try:      # 控制台默认 GBK：自检里的 ✔/✘ 一旦被重定向就 UnicodeEncodeError 崩掉整条自检
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image                        # noqa: E402

from agent import image_lib as il            # noqa: E402

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s  %s" % (name, detail))


root = tempfile.mkdtemp(prefix="img_lib_")
lib = os.path.join(root, "assets", "anime")
os.makedirs(lib, exist_ok=True)


def mk(name, size=(40, 30)):
    p = os.path.join(lib, name)
    Image.new("RGB", size, (200, 120, 90)).save(p)
    return p


print("① 空库：给的是可读文案，不是异常")
cfg = {"image_reply": {"enabled": True, "mode": "local", "dir": "assets/anime", "max_mb": 8,
                       "min_gap_seconds": 0, "avoid_recent": 3, "include_gif": True}}
ck("scan 空库返回 []", il.scan(cfg, root) == [])
p, why = il.pick(cfg, root)
ck("pick 空库返回 (None, 文案)", p is None and "图库是空的" in why, why)
st = il.status(cfg, root)
ck("status 提供可读文案", "图库 0 张" in st["text"], st["text"])

print("② 有图：扫描 + 随机 + 不重复")
paths = [mk("a.png"), mk("b.png"), mk("c.jpg"), mk("d.gif")]
ck("扫描到 4 张", len(il.scan(cfg, root)) == 4, il.scan(cfg, root))
picked = set()
for _ in range(6):
    q, _w = il.pick(cfg, root)
    picked.add(os.path.basename(q))
ck("多次 pick 会换图（≥2 张不同）", len(picked) >= 2, picked)
ck("最近记录被落盘", len(il._load_recent(root).get("recent") or []) > 0)
ck("最近记录上限生效（avoid_recent=3 ⇒ 最多 3 条）",
   len(il._load_recent(root).get("recent") or []) <= 3, il._load_recent(root).get("recent"))

print("③ include_gif=False 时排除 gif")
cfg2 = dict(cfg)
cfg2["image_reply"] = dict(cfg["image_reply"], include_gif=False)
names = [os.path.basename(x) for x in il.scan(cfg2, root)]
ck("gif 被排除", "d.gif" not in names and len(names) == 3, names)

print("④ 超限图片被跳过（max_mb 极小 ⇒ 全跳过 ⇒ 明确文案）")
cfg3 = dict(cfg)
cfg3["image_reply"] = dict(cfg["image_reply"], max_mb=0.00001)
q, why3 = il.pick(cfg3, root)
ck("超限 ⇒ 返回 None + 空库文案", q is None and "图库是空的" in why3, why3)

print("⑤ 总开关 / 冷却 / 在线图源未配置")
cfg4 = dict(cfg)
cfg4["image_reply"] = dict(cfg["image_reply"], enabled=False)
q, why4 = il.next_image(cfg4, root, "chat1")
ck("开关关着 ⇒ 明确说没开", q is None and "关闭" in why4, why4)
cfg5 = dict(cfg)
cfg5["image_reply"] = dict(cfg["image_reply"], enabled=True, min_gap_seconds=60)
il.pick(cfg5, root, "chat1")                                   # 记一次 last
ck("冷却中 ⇒ 剩余秒数 > 0", il.cooldown_left(cfg5, "chat1", root) > 0)
ck("别的会话不受冷却影响", il.cooldown_left(cfg5, "chat2", root) == 0)
q, why5 = il.next_image(cfg5, root, "chat1")
ck("冷却中被拦住并给出原因", q is None and "间隔" in why5, why5)
cfg6 = dict(cfg)
cfg6["image_reply"] = dict(cfg["image_reply"], enabled=True, mode="api", api_url="", min_gap_seconds=0)
q, why6 = il.next_image(cfg6, root, "chat9")
ck("api 模式未填地址 ⇒ 提示改用本地", q is None and "没配置" in why6, why6)
q, why7 = il.fetch_api({"image_reply": {"api_url": "http://127.0.0.1:9/none", "api_timeout_ms": 1500}}, root)
ck("图源不可达 ⇒ 返回原因不抛异常", q is None and "失败" in why7, why7)

import shutil                                 # noqa: E402
shutil.rmtree(root, ignore_errors=True)
print("\n== 结论：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
